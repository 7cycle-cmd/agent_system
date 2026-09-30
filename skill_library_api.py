"""Skill Library + RBAC + Worker Plan Mode.

Implements the user's plan:
1. RBAC (roles / users) + Skill Library (skills / skill_versions) + llm_tasks tables
   (SQLite, created by db_schema.ensure_task_center_schema).
2. Skill Library API under /api/v1 with Bearer-token RBAC.
3. Worker Ask -> Confirm -> Plan flow (Plan Mode produces plan only, no writes).
4. Worker Plan Mode system prompt (ontology_task_planner bundle).
5. Python validation layer (hard rules, not LLM).

Auth: Authorization: Bearer {api_token} -> user -> role -> permissions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# IDENTITY INVERSION 2026-09-24: the IDENTITY is `identity_registry`; the
# `chat_main` row is the OUTPUT FORMAT. The provider->worker rule lives in ONE
# place (`chat_identity_backfill`) so there is no second implementation.
import chat_identity_backfill as _cb  # noqa: E402
import identity_registry as _ir  # noqa: E402
import worker_registry as _wr  # noqa: E402
from identity_registry import IdentityRefused  # noqa: E402

_wr_registry_worker = _wr.register_worker

# The workflow an identity for a chat belongs to. MEASURED: the repo's existing
# chat identity uses workflow 2 (`identity_registry` row 1), reached by the
# route `chat_identity.worker_identity_flow` (purpose_route_registry).
_IDENTITY_WORKFLOW_ID = 2

# The ONLY `source` values that RECORD a purpose. Anything else keeps the
# NOT-ANSWERED sentinel ('NA') and is reported -- never fabricated.
_PURPOSE_FROM_SOURCE: dict[str, str] = dict(_cb.SOURCE_TO_PURPOSE)


def _purpose_for(source: str | None, why: str | None) -> str:
    """The purpose for a chat: an EXPLICIT one, else the one its source RECORDS.

    Returns '' when neither exists — the caller REFUSES rather than inventing a
    purpose, because an identity with no purpose cannot be routed to a service
    (the user: "chatting is for purpose -> we need to provide services").
    """
    explicit = str(why or "").strip()
    if explicit and explicit.upper() != "NA":
        return explicit
    return _PURPOSE_FROM_SOURCE.get(str(source or "").strip(), "")

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
AGENT_DB_PATH = BASE_DIR / "agent.db"

# P0-1 write-owner gate: chat_identity_log has exactly one authorized writer.
# SKILL-0001 (hash/validate) must NOT write; SKILL-0002 (persist) is the sole
# authorized entry. This is what stops the old double-write bug recurring.
CHAT_IDENTITY_TABLE = "chat_identity_log"
CHAT_IDENTITY_WRITE_OWNER = "SKILL-0002"
CHAT_IDENTITY_CONTRACT = "SKILL-0001"

# P0-4 / P0-5 gates are ON by default (A3-flip, 2026-09-20).
#
# WHY THE DEFAULT FLIPPED
# -----------------------
# The old default was OFF because every contract sat below its 100-streak
# target, so enabling the gates with an EMPTY allowlist made
# resolve_chat_identity() / register_chat_identity() return
# DEPENDENCY_NOT_READY for every call — a production outage, not enforcement.
#
# That premise is now FALSE and was re-measured before flipping:
#   * C2-followup changed the target from 100 to 5 and made the streak count
#     DISTINCT code states, so it no longer inflates on repetition.
#   * All 17 contracts are streak-qualified.
#   * SKILL-0001 current_streak=27 >= target 5; SKILL-0002 current_streak=24
#     >= target 5; SKILL-0001's declared dependency SKILL-0002 exists AND is
#     qualified. So both gates PASS on the live data.
#
# WHY THE ALLOWLIST IS *NOT* SET
# ------------------------------
# The allowlist EXEMPTS a contract from the gate. Setting it for SKILL-0001 /
# SKILL-0002 would leave the gates ON but bypassed for exactly the contracts
# they exist to protect — a gate that is enabled and simultaneously disabled,
# which is the "rule that does not apply" defect in a new costume. The
# allowlist stays EMPTY so the gates actually apply.
#
# The allowlist remains available as a diagnostic escape hatch
# (skill_contract_store.is_gate_allowlisted, env
# SKILL_CONTRACT_GATE_ALLOWLIST="SKILL-0001,SKILL-0002"). Use it only to run a
# scenario that must bypass the gate, and say so.
#
# Set either env var to "0" to disable that gate for a diagnostic run.
ENFORCE_DEPENDENCY_GATE = os.environ.get("SKILL_CONTRACT_ENFORCE_DEPS", "1") == "1"
ENFORCE_STREAK_GATE = os.environ.get("SKILL_CONTRACT_ENFORCE_STREAK", "1") == "1"

# Ontology hierarchy (fixed order) — matches src/task_center/ontology_store.py
ONTOLOGY_ORDER = [
    "Channel",
    "Module",
    "Capability",
    "API",
    "Function",
    "Table",
    "Field",
]
OPTIONAL_TYPES = ["Job", "Event"]
ALL_TYPES = ONTOLOGY_ORDER + OPTIONAL_TYPES
VALID_ACTIONS = {"CREATE", "UPDATE", "DELETE"}

# State machine: New -> Ask -> Confirm -> Plan -> Completed
STAGE_TRANSITIONS: dict[str, set[str]] = {
    "new": {"ask"},
    "ask": {"confirm", "ask"},            # ask may re-run only from ask
    "confirm": {"plan", "confirm", "ask"},  # user modify resets to ask
    "plan": {"completed", "ask"},
    "completed": set(),
}
# Job/Event internal sort priority (stable task tree)
OPTIONAL_SORT = {"Job": 0, "Event": 1}

# ---- TTL heartbeat monitoring config ----
HEARTBEAT_SESSION_ID = "__ttl__"
HEARTBEAT_ACTION = "ttl_cleanup"
HEARTBEAT_MAX_AGE = 3600  # seconds; alert if no heartbeat for >1h
HEARTBEAT_CHECK_INTERVAL = 300  # monitor polls every 5 min
HEARTBEAT_ALERT_ENABLE = True
_last_heartbeat_alert_firing: bool = False

# Standardized error codes (documented for worker callers)
PLAN_ERROR_CODES: dict[str, str] = {
    "MISSING_FIELDS": "required fields missing (session_id / requirement)",
    "INVALID_STAGE_TRANSITION": "illegal state-machine jump (e.g. generate before confirm)",
    "VERSION_CONFLICT": "optimistic-lock conflict: stale expected_version",
    "DUPLICATE_ENTITY": "duplicate entity name in user_modify.add",
    "SESSION_NOT_FOUND": "session_id does not exist",
    "INVALID_CHAT_ID": "chat_id is not a 64-char lowercase SHA256",
    "CHAT_NOT_FOUND": "chat_id not found in chat_reply_log",
    "INTERNAL_ERROR": "unexpected server error",
}

_SESSION_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(session_id: str, chat_id: str = "") -> threading.Lock:
    key = (session_id, chat_id)
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


def chat_pair_hash(chat_id: str, session_id: str) -> str:
    """SHA256 of (chat_id, session_id) — the PAIR relationship key."""
    return hashlib.sha256(
        f"{chat_id}|{session_id}".encode("utf-8")
    ).hexdigest()


def is_valid_chat_hash(chat_id: str) -> bool:
    """True if chat_id is a 64-char lowercase hex SHA256 (PAIR-03)."""
    return bool(re.fullmatch(r"[0-9a-f]{64}", chat_id or ""))


def chat_id_exists(chat_id: str) -> bool:
    """True if chat_id is registered (PAIR-02 front-gate).

    Accepts a chat_id that is present in the chat_id registry table OR in
    chat_reply_log (real chat sessions). This lets both pre-registered ids
    (via ensure_chat_id / migration) and ids from actual chat logs pass.
    """
    if not chat_id:
        return False
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM chat_id WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT 1 FROM chat_reply_log WHERE chat_id=? LIMIT 1", (chat_id,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def ensure_chat_id(chat_id: str) -> bool:
    """Register a chat_id row (idempotent). Returns True if valid/non-empty."""
    if not chat_id:
        return False
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO chat_id (chat_id) VALUES (?)", (chat_id,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---- chat_identity: DB-driven resolve / register + audit log ----
# chat_id      = INTEGER chat_main.id (auto-increment PK) — the identity + FK.
# sha256       = sha256(session_id) — a content HASH, NOT an id (display/dedupe).
# chat_hash    = sha256(f"{chat_id}|{session_id}") — the PAIR key (plan_sessions).

SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_valid_session_id(session_id: str) -> bool:
    """True if session_id is a canonical 8-4-4-4-12 UUID."""
    return bool(SESSION_ID_RE.fullmatch((session_id or "").strip()))


# The WORKFLOW status of a chat turn. A CLOSED vocabulary, because a status a
# reader cannot enumerate is a status nobody can filter or count.
#
#   draft       written down, NOT yet reviewed by the human
#   done        finished, and every claim carries evidence
#   ask         a specific decision is unanswered AND the worker STOPPED
#   progressive work in flight, nothing being waited on
#   QC          finished, awaiting an INDEPENDENT verdict
#
# `ask` is NOT "the reply contains a question". It is "a question AND the
# worker stopped" -- a worker that asks and keeps working is `progressive`.
# That distinction is what makes the status mechanically checkable instead of
# a matter of reading tone.
#
# `draft` (added 2026-09-25). THE HUMAN: "register my message at chat ... +
# status = draft / so i can step to step to look into that". It is NOT a synonym
# of `progressive`: `progressive` means the WORKER is still working, while
# `draft` means the worker has STOPPED and the text is waiting for the human to
# read it. A reader filtering for "what is waiting on me" needs the second, and
# `progressive` would hide it.
#
# THE HUMAN'S TURN LIFECYCLE (added 2026-09-25):
#     "default = draft -> send to logic generator to have it to be the output as
#      format and status -> pending"
#     "status : draft / penping / progressing / completed"
#
#   draft        the DEFAULT. Written down, not yet sent anywhere.
#   pending      SENT to the logic generator, not yet answered.
#   progressing  the generator is RUNNING.
#   completed    the generator returned a format AND a status.
#
# The four EXISTING values stay: MEASURED, `chat_center_message.status` already
# holds `done` 161 and `ask` 3, so dropping them would orphan 164 rows. The
# vocabulary is the UNION, and the human's four are the turn lifecycle.
#
# `research` ADDED 2026-09-30 (plan CHAT.PIPELINE.S6, S6.4a). It is the FIRST
# stage of the 9-stage conversation pipeline, and it is a REGISTERED TERM
# (`terminology_registry` term_id 1530, cite `research_direction.py:65`). It is
# added HERE because `chat_level.set_conversation_status` checks this vocabulary
# BEFORE it checks `CONVERSATION_TRANSITIONS`, so a transition to `research`
# would be refused at the first gate even with the transition declared. The
# value is APPENDED, never substituted: every existing value keeps its position
# and meaning, so no stored row changes meaning.
WORKFLOW_STATUSES: tuple[str, ...] = (
    "draft", "research", "pending", "progressing", "completed",
    "done", "ask", "progressive", "QC",
)

# The human's TURN lifecycle, in order. A subset of WORKFLOW_STATUSES, named
# separately so a reader can tell "the 4 states a turn moves through" from "every
# status this repo has ever stored".
TURN_STATUSES: tuple[str, ...] = ("draft", "pending", "progressing", "completed")

# The STEP status of a workflow step. THE HUMAN:
#     "id | researching / id | writing / id | verfitiy"
#
# MEASURED: `workflow_step` had NO status column, and the nearest existing
# vocabulary is `task_lifecycle_log.task_state` (draft / validating /
# researching / validated / proposal_draft), so `researching` is NOT invented.
# `writing` and `verifying` are the human's own words.
STEP_STATUSES: tuple[str, ...] = ("researching", "writing", "verifying")


def sha256_from_session(session_id: str) -> str:
    """Content hash = sha256(session_id). NOT an id — see chat_main.id."""
    return hashlib.sha256((session_id or "").strip().encode("utf-8")).hexdigest()


def chat_id_from_session(session_id: str) -> str:
    """DEPRECATED alias of sha256_from_session (kept for f_copy_reply callers).

    NOTE: this returns the HASH, not a chat id. Chat ids are now INTEGER
    (chat_main.id). Prefer sha256_from_session() for new code.
    """
    return sha256_from_session(session_id)


def _chat_main_ensure(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    ide: str | None = None,
    llm: str | None = None,
    source: str = "api",
    why: str,
) -> int:
    """Find or create the chat row for a session. Returns the INTEGER id.

    INVERTED 2026-09-24 (user: "not by new module to totally replace chat_main?"
    -> "**b** and **totally remove old when new proofed").

    `identity_registry` is now written FIRST and is the IDENTITY; `chat_main`
    is the OUTPUT FORMAT, created only to hand back an INTEGER id for the
    existing `chat_id` references. The worker is DERIVED from the recorded
    provider (never invented) via the SAME derivation the backfill uses, so
    there is no second implementation of the provider rule.

    `why` (the PURPOSE) is REQUIRED and is not defaulted here: a chat with no
    purpose cannot be routed to a service, so the caller resolves it and the
    public entry point REFUSES when it is absent.
    """
    sha = sha256_from_session(session_id)
    prov = _cb.derive_worker(conn, {"id": 0, "ide": ide, "llm": llm,
                                    "source": source})
    if not prov.get("ok"):
        # NO PRODUCER WITHOUT AN IDENTITY: a chat that cannot name its worker
        # is not written, because inventing a worker would fabricate an
        # identity. The caller must supply a recorded provider (ide/llm).
        # NOTE: IdentityRefused takes a LIST of reasons (passing a bare string
        # makes it join the characters -- caught by the proof).
        raise IdentityRefused(
            ["cannot derive a provider worker for session %s" % session_id[:16],
             str(prov.get("why"))])
    wres = _wr_registry_worker(
        conn, worker_key=prov["worker_key"], name=prov["name"],
        worker_type=prov["worker_type"], capability_ref="provider.transport",
        physical_path=prov["app_key"], uses_text=prov["cite"],
        cite_ref=prov["cite"])
    if not wres.get("ok") or not (wres.get("worker") or {}).get("worker_id"):
        raise IdentityRefused(["provider worker %r could not be registered"
                               % prov["worker_key"], str(wres)])

    # THE IDENTITY FIRST. The identity is NOT derived from the output row, and
    # `open_identity` does not need `chat_id` (it stays NULL until linked), so
    # the identity genuinely comes first — the OUTPUT is written afterwards.
    # `open_identity` REFUSES a blank/NA purpose, so the refusal is surfaced
    # rather than stored as if the purpose had been answered.
    #
    # MEASURED (2026-09-25), AND THIS WROTE THE WRONG FACT: it passed
    # `channel=str(prov["app_key"])`, so `identity_registry.channel` held
    # `vscode` -- an APP KEY, not a channel. THE USER: "vscode not channel!!! /
    # channel is local > agent_system / vscode is working enviornment". The
    # channel is READ from `channel_registry` (the ONE declared channel), and
    # the app key is kept on the WORKER (`physical_path`), where it belongs.
    import channel_registry as _cr
    _chans = _cr.list_channels(conn).get("channels") or []
    _channel = str(_chans[0]["channel_key"]) if _chans else "NA"
    res = _ir.open_identity(
        conn, session_id=session_id, worker_key=prov["worker_key"],
        workflow_id=_IDENTITY_WORKFLOW_ID, channel=_channel,
        why=why,
        cite_ref="%s; %s" % (prov["cite"], "measured: skill_library_api.py:"
                             "register_chat_identity source=%r" % source))

    # THE OUTPUT FORMAT: the `chat_main` row, created only to hand back the
    # INTEGER id the existing `chat_id` references need.
    #
    # `chat_main.llm` IS DERIVED FROM THE REGISTER (QC-08, 2026-09-25).
    # MEASURED DEFECT: this wrote the CALLER-SUPPLIED `llm` verbatim, so
    # `chat_main.llm` held `copilot` — a value a caller typed — while the
    # register held `DeepSeek V4.1 Flash 0731`. Two sources for one fact, and
    # they disagreed on 2 of the 2 filled rows.
    #
    # The FACT is `identity_registry.llm_id`; the name is a DISPLAY of it. So
    # the name is read back through the join. A caller-supplied `llm` is used
    # ONLY when the register has no assignment yet, and it is then a
    # placeholder, not a claim.
    import identity_llm as _il
    _reg_name = ""
    try:
        _out = _il.llm_for_session(conn, session_id)
        for _r in (_out.get("rows") or []):
            _n = str(_r.get("llm_name") or "").strip()
            if _n:
                _reg_name = _n
                break
    except Exception:
        _reg_name = ""
    _llm_value = _reg_name or llm
    row = conn.execute(
        "SELECT id FROM chat_main WHERE session_id=?", (session_id,)).fetchone()
    if row:
        mid = int(row[0])
        # MEASURED (2026-09-26): this used to write
        # `chat_hash = chat_pair_hash(str(mid), session_id)` where `mid` is
        # `chat_main.id` — the CONVERSATION row id, not the CHAT id. MEASURED:
        # 58 of 58 non-NULL `chat_hash` values are `sha256(chat_main.id |
        # session_id)`, and 0 are `sha256(chat_main.chat_id | session_id)`.
        #
        # `chat_main.id` is the PRIMARY KEY, so that hash is a re-encoding of the
        # row id — a pair key that pairs nothing, mis-declared `kind='pair_key'`.
        #
        # The writer CANNOT compute the real pair key here: `set_chat_id`'s own
        # docstring says "the chat does not exist until the workflow runs", so
        # `chat_main.chat_id` is still NULL at identity-open time. The pair key
        # is therefore owned by `db_schema._backfill_chat_hash_recomputed`, which
        # fills `chat_hash_recomputed` once `chat_id` exists. This writer stops
        # writing the wrong half and leaves `chat_hash` as append-only audit.
        conn.execute(
            """
            UPDATE chat_main
               SET sha256=?, ide=COALESCE(?, ide),
                   llm=COALESCE(?, llm), updated_at=CURRENT_TIMESTAMP
             WHERE id=?
            """,
            (sha, ide, _llm_value, mid),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO chat_main (session_id, sha256, ide, llm, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, sha, ide, _llm_value, source),
        )
        mid = int(cur.lastrowid)
    _ir.set_chat_id(conn, res["identity"]["identity_key"], mid,
                    cite_ref="measured: chat_main.id=%d is this session's row"
                             % mid)
    # Legacy bridge so old TEXT chat_id values stay resolvable.
    conn.execute(
        "INSERT OR IGNORE INTO chat_main_hash (hash, chat_main_id) VALUES (?, ?)",
        (sha, mid),
    )
    conn.commit()
    return mid


def _log_chat_identity(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    chat_id: int | None,
    sha256: str | None,
    chat_hash: str | None,
    action: str,
    ide: str | None = None,
    llm: str | None = None,
    source: str = "api",
    caller_contract_id: str = CHAT_IDENTITY_WRITE_OWNER,
) -> None:
    """Append one chat_identity_log row.

    P0-1 write-owner gate: only the declared write owner (SKILL-0002) may write
    chat_identity_log. A non-owner caller is rejected BEFORE any INSERT, so the
    old double-write bug (SKILL-0001 + SKILL-0002 both writing) cannot recur.
    """
    if not _assert_chat_identity_write_allowed(caller_contract_id):
        return
    # P0-3 payload gate: validate against SKILL-0001's Field Register.
    # created_at is server time (the Field Register forbids client-supplied
    # time), so it is stamped here rather than accepted from the caller.
    # build_chat_identity_payload() is shared with the chat_center write path so
    # both paths cannot drift apart.
    ok_payload, payload_errors = _assert_chat_identity_payload_valid(
        build_chat_identity_payload(
            session_id=session_id,
            chat_id=chat_id,
            sha256=sha256,
            action=action,
        )
    )
    if not ok_payload:
        logger.warning("chat_identity_log write skipped (payload): %s", payload_errors)
        return
    try:
        conn.execute(
            """
            INSERT INTO chat_identity_log
                (session_id, chat_id, sha256, chat_hash, action, ide, llm, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, chat_id, sha256, chat_hash, action, ide, llm, source),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def _utc_now_iso() -> str:
    """Server-side ISO8601 UTC timestamp (Field Register forbids client time)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_chat_identity_write_allowed(caller_contract_id: str) -> bool:
    """Hard gate for chat_identity_log writes. Returns True when allowed.

    Fails OPEN only when the contract SSOT is unavailable (no tables / import
    error), so a DB without the contract schema is not bricked. When the SSOT
    is present and an owner is declared, a non-owner is rejected.
    """
    try:
        import skill_contract_store as scs

        ok, reason = scs.assert_table_write_allowed(
            CHAT_IDENTITY_TABLE, caller_contract_id
        )
        if not ok:
            logger.warning("chat_identity_log write blocked: %s", reason)
        return ok
    except Exception as e:  # SSOT unavailable -> do not brick the write path
        logger.debug("write-owner gate skipped (%s)", type(e).__name__)
        return True


def _assert_chat_identity_payload_valid(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """P0-3: hard-validate the payload against SKILL-0001's Field Register.

    Fails OPEN when the contract SSOT is unavailable, so a DB without the
    contract schema keeps working.
    """
    try:
        import skill_contract_store as scs

        ok, errors = scs.validate_payload_against_contract(
            CHAT_IDENTITY_CONTRACT, payload
        )
        if not ok:
            logger.warning("chat_identity payload rejected: %s", errors)
        return ok, errors
    except Exception as e:
        logger.debug("payload gate skipped (%s)", type(e).__name__)
        return True, []


# ---- public gate surface -------------------------------------------------
# chat_identity_log has MORE THAN ONE write path (skill_library_api and
# mouse_spot_helper's chat_center flow). Both must pass the same two hard
# gates, otherwise "SKILL-0002 is the sole write entry" is only half-true.
# These wrappers exist so a second caller reuses ONE implementation instead of
# re-deriving the rules (which is how the ungated path appeared in the first
# place).


def assert_chat_identity_write_allowed(
    caller_contract_id: str = CHAT_IDENTITY_WRITE_OWNER,
) -> bool:
    """P0-1 gate, callable from any chat_identity_log write path."""
    return _assert_chat_identity_write_allowed(caller_contract_id)


def assert_chat_identity_payload_valid(
    payload: dict[str, Any],
) -> tuple[bool, list[str]]:
    """P0-3 gate, callable from any chat_identity_log write path."""
    return _assert_chat_identity_payload_valid(payload)


def build_chat_identity_payload(
    *,
    session_id: str | None,
    chat_id: int | None,
    sha256: str | None,
    action: str,
) -> dict[str, Any]:
    """Build the Field-Register payload for a chat_identity_log row.

    `created_at` is stamped server-side because the Field Register forbids a
    client-supplied time; `role` is derived from `action` so the two can never
    disagree.
    """
    return {
        "chat_id": str(chat_id) if chat_id is not None else "",
        "trace_id": str(session_id or ""),
        "action": action,
        "role": chat_identity_role(action),
        "sha256_hash": str(sha256 or ""),
        "created_at": _utc_now_iso(),
    }


def _assert_chat_identity_dependencies_ready() -> tuple[bool, str]:
    """P0-4: declared dependencies must exist and be streak-qualified.

    OPT-IN (default OFF). A contract named in SKILL_CONTRACT_GATE_ALLOWLIST is
    exempt, which is how test scenarios run without the gate blocking them.
    """
    if not ENFORCE_DEPENDENCY_GATE:
        return True, ""
    try:
        import skill_contract_store as scs

        if scs.is_gate_allowlisted(CHAT_IDENTITY_CONTRACT):
            logger.debug("dependency gate allowlisted for %s", CHAT_IDENTITY_CONTRACT)
            return True, ""
        ok, reason = scs.assert_dependencies_ready(CHAT_IDENTITY_CONTRACT)
        if not ok:
            logger.warning("chat_identity dependency gate blocked: %s", reason)
        return ok, reason
    except Exception as e:
        logger.debug("dependency gate skipped (%s)", type(e).__name__)
        return True, ""


def _assert_chat_identity_streak_qualified() -> tuple[bool, str]:
    """P0-5: the contract must be streak-qualified before production use.

    OPT-IN (default OFF). A contract named in SKILL_CONTRACT_GATE_ALLOWLIST is
    exempt, which is how test scenarios run without the gate blocking them.
    """
    if not ENFORCE_STREAK_GATE:
        return True, ""
    try:
        import skill_contract_store as scs

        if scs.is_gate_allowlisted(CHAT_IDENTITY_CONTRACT):
            logger.debug("streak gate allowlisted for %s", CHAT_IDENTITY_CONTRACT)
            return True, ""
        ok, reason = scs.assert_streak_qualified(CHAT_IDENTITY_CONTRACT)
        if not ok:
            logger.warning("chat_identity streak gate blocked: %s", reason)
        return ok, reason
    except Exception as e:
        logger.debug("streak gate skipped (%s)", type(e).__name__)
        return True, ""


def chat_identity_role(action: str) -> str:
    """Derive role from action: GET (resolve/miss) = Question, POST (register) = Answer."""
    return "Answer" if (action or "") == "register" else "Question"


def resolve_chat_identity(
    session_id: str,
    *,
    source: str = "api",
    log: bool = True,
    ide: str | None = None,
    llm: str | None = None,
) -> dict[str, Any]:
    """Resolve session_id -> chat_id (INTEGER chat_main.id). Logs resolve/miss.

    Returns {ok, session_id, chat_id, sha256, chat_hash, registered, source}.
    """
    sid = (session_id or "").strip()
    if not is_valid_session_id(sid):
        return {
            "ok": False,
            "error_code": "INVALID_SESSION_ID",
            "error": "session_id must be a canonical UUID (8-4-4-4-12)",
            "session_id": sid,
        }
    # P0-4 / P0-5 gates (opt-in via env vars; see module constants).
    ok_dep, dep_reason = _assert_chat_identity_dependencies_ready()
    if not ok_dep:
        return {"ok": False, "error_code": "DEPENDENCY_NOT_READY",
                "error": dep_reason, "session_id": sid}
    ok_streak, streak_reason = _assert_chat_identity_streak_qualified()
    if not ok_streak:
        return {"ok": False, "error_code": "STREAK_NOT_QUALIFIED",
                "error": streak_reason, "session_id": sid}
    sha = sha256_from_session(sid)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM chat_main WHERE session_id=?", (sid,)
        ).fetchone()
        registered = row is not None
        chat_id = int(row[0]) if row else None
        if chat_id is None:
            # read-only resolve must not create; fall back to the legacy hash
            leg = conn.execute(
                "SELECT chat_main_id FROM chat_main_hash WHERE hash=?", (sha,)
            ).fetchone()
            if leg and leg[0] is not None:
                chat_id = int(leg[0])
                registered = True
        chat_hash = chat_pair_hash(str(chat_id), sid) if chat_id is not None else None
        if log:
            _log_chat_identity(
                conn,
                session_id=sid,
                chat_id=chat_id,
                sha256=sha,
                chat_hash=chat_hash,
                action="resolve" if registered else "miss",
                ide=ide,
                llm=llm,
                source=source,
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "session_id": sid,
        "chat_id": chat_id,
        "sha256": sha,
        "chat_hash": chat_hash,
        "registered": registered,
        "role": "Question",
        "source": source,
    }


def register_chat_identity(
    session_id: str,
    *,
    source: str = "api",
    ide: str | None = None,
    llm: str | None = None,
    why: str | None = None,
) -> dict[str, Any]:
    """Register session_id -> chat_id (INTEGER, idempotent). Logs a register row.

    `why` (the PURPOSE) is REQUIRED, directly or RECORDED by `source`. A chat
    with no purpose is REFUSED with `PURPOSE_REQUIRED`: it cannot be routed to a
    service, so writing it would create an identity that looks valid and is
    unroutable (the user: "chatting is for purpose -> we need to provide
    services, which services can match the user").
    """
    sid = (session_id or "").strip()
    if not is_valid_session_id(sid):
        return {
            "ok": False,
            "error_code": "INVALID_SESSION_ID",
            "error": "session_id must be a canonical UUID (8-4-4-4-12)",
            "session_id": sid,
        }
    # P0-4 / P0-5 gates (opt-in via env vars; see module constants).
    ok_dep, dep_reason = _assert_chat_identity_dependencies_ready()
    if not ok_dep:
        return {"ok": False, "error_code": "DEPENDENCY_NOT_READY",
                "error": dep_reason, "session_id": sid}
    ok_streak, streak_reason = _assert_chat_identity_streak_qualified()
    if not ok_streak:
        return {"ok": False, "error_code": "STREAK_NOT_QUALIFIED",
                "error": streak_reason, "session_id": sid}
    # THE PURPOSE (why). A chat with no purpose cannot be routed to a service,
    # so it is refused HERE rather than stored as an unroutable identity.
    purpose = _purpose_for(source, why)
    if not purpose:
        return {
            "ok": False,
            "error_code": "PURPOSE_REQUIRED",
            "error": ("no purpose is recorded or supplied for this chat: "
                      "pass why=... or use a source that records one (%s). "
                      "A chat without a purpose cannot be routed to a "
                      "service, so none is invented."
                      % sorted(_PURPOSE_FROM_SOURCE)),
            "session_id": sid,
            "source": source,
        }
    sha = sha256_from_session(sid)
    conn = _conn()
    try:
        existed = (
            conn.execute(
                "SELECT 1 FROM chat_main WHERE session_id=?", (sid,)
            ).fetchone()
            is not None
        )
        chat_id = _chat_main_ensure(
            conn, session_id=sid, ide=ide, llm=llm, source=source,
            why=purpose,
        )
        chat_hash = chat_pair_hash(str(chat_id), sid)
        _log_chat_identity(
            conn,
            session_id=sid,
            chat_id=chat_id,
            sha256=sha,
            chat_hash=chat_hash,
            action="register",
            ide=ide,
            llm=llm,
            source=source,
        )
    finally:
        conn.close()
    return {
        "ok": True,
        "session_id": sid,
        "chat_id": chat_id,
        "sha256": sha,
        "chat_hash": chat_hash,
        "created": not existed,
        "role": "Answer",
        "source": source,
        "why": purpose,
    }


def lookup_chat_identity_by_chat_id(chat_id) -> dict[str, Any]:
    """Reverse lookup: chat_id (int id or legacy sha256) -> session_ids."""
    raw = str(chat_id or "").strip()
    # Accept legacy sha256 text or the new integer id.
    if not raw:
        return {
            "ok": False,
            "error_code": "INVALID_CHAT_ID",
            "error": "provide chat_id (integer id or sha256)",
            "chat_id": raw,
        }
    cid: int | None = None
    try:
        cid = int(raw)
    except ValueError:
        cid = None
    conn = _conn()
    try:
        if cid is None:
            leg = conn.execute(
                "SELECT chat_main_id FROM chat_main_hash WHERE hash=? LIMIT 1",
                (raw.lower(),),
            ).fetchone()
            if leg and leg[0] is not None:
                cid = int(leg[0])
            else:
                row = conn.execute(
                    "SELECT id FROM chat_main WHERE sha256=? LIMIT 1", (raw.lower(),)
                ).fetchone()
                if row:
                    cid = int(row[0])
        sessions: list[str] = []
        if cid is not None:
            for r in conn.execute(
                """
                SELECT DISTINCT session_id FROM chat_identity_log
                WHERE chat_id=? AND session_id IS NOT NULL AND session_id<>''
                """,
                (cid,),
            ).fetchall():
                sessions.append(str(r[0]))
            row = conn.execute(
                "SELECT session_id, sha256 FROM chat_main WHERE id=?", (cid,)
            ).fetchone()
            if row and row[0] and str(row[0]) not in sessions:
                sessions.append(str(row[0]))
            sha = str(row[1]) if row and row[1] else None
            registered = True
        else:
            for r in conn.execute(
                "SELECT DISTINCT session_id FROM chat_reply_log WHERE chat_id=?",
                (raw,),
            ).fetchall():
                if r[0] and str(r[0]) not in sessions:
                    sessions.append(str(r[0]))
            sha = None
            registered = False
    finally:
        conn.close()
    return {
        "ok": True,
        "chat_id": cid if cid is not None else raw,
        "sha256": sha,
        "registered": registered,
        "sessions": sessions,
    }


def recent_chat_identities(limit: int = 50) -> dict[str, Any]:
    """Recent chat_identity_log rows (newest first) for the UI table.

    THE HUMAN (2026-09-25), on /llm-tasks/chat_identity/recent:
        "remove sha256 / chat_hash (pair) / chat_hash (pair) at the table"
        "+ image = environment, display environment = kind + product + surface"
        "update LLM = LLM table !!!! real name , example vscode > LLM is
         deepseek V4.1 Flash"
        "and where is status!!!!"

    MEASURED, and these are the three defects this fixes:

    1. `llm` was the FREE TEXT `copilot` on every row. MEASURED: the register
       holds the real name (`llm_model` id=4 = `DeepSeek V4.1 Flash 0731`) and
       `identity_registry.llm_id` was NULL in ALL 55 rows, so the link existed
       and was never used. `llm_name` now comes from `llm_model` via `llm_id`,
       and an unassigned session reports `""` — NEVER the free text.

    2. There was NO environment. MEASURED: `working_environment` already stores
       `kind + product + surface` (and `display` IS that formula), and no chat
       table linked to it. `environment_display` now comes from the STORED
       `identity_registry.environment_id`, so the read is a plain FK join and
       cannot drift the way a name join would.

    3. There was NO status. `status` now comes from the turn's
       `chat_center_message` row for the same session.

    `sha256` / `chat_hash` are still RETURNED (other callers use them) but the UI
    table no longer renders them.
    """
    lim = max(1, min(int(limit or 50), 500))
    conn = _conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT l.id, l.session_id, l.chat_id, l.sha256,
                       l.chat_hash_recomputed, l.action, l.ide, l.llm,
                       l.source, l.created_at,
                       ir.environment_id AS environment_id,
                       we.kind           AS environment_kind,
                       we.product        AS environment_product,
                       we.surface        AS environment_surface,
                       we.display        AS environment_display,
                       lm.name           AS llm_name,
                       lm.model_id       AS llm_model_id,
                       (SELECT m.status FROM chat_center_message m
                         WHERE m.session_id = l.session_id
                         ORDER BY m.id DESC LIMIT 1) AS status
                FROM chat_identity_log l
                LEFT JOIN identity_registry ir
                       ON ir.session_id = l.session_id AND ir.is_active = 1
                LEFT JOIN working_environment we
                       ON we.environment_id = ir.environment_id
                LEFT JOIN llm_model lm
                       ON lm.id = ir.llm_id
                ORDER BY l.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        ]
        for r in rows:
            r["role"] = chat_identity_role(r.get("action"))
            # The register's name WINS. The free text is kept under a DIFFERENT
            # key so a reader can see the disagreement instead of it being
            # silently overwritten.
            r["llm_free_text"] = r.get("llm")
            r["llm"] = r.get("llm_name") or ""
        total = conn.execute(
            "SELECT COUNT(*) FROM chat_identity_log"
        ).fetchone()[0]
        registered = conn.execute(
            "SELECT COUNT(*) FROM chat_id"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "total": int(total),
        "registered_total": int(registered),
    }


def resolve_environment_id(conn: Any, *names: str | None) -> dict[str, Any]:
    """Resolve a chat to a `working_environment.environment_id`. EXACT ONLY.

    MEASURED, and this is why it exists: the only path from a chat to an
    environment was a FUZZY JOIN BY NAME —
        identity_registry.channel='local_pc'
          -> channel_registry.name='Local PC'
          -> working_environment.product='Local PC'
    A name join is this repo's recurring defect #4 ("a fuzzy match joins by
    luck"). This resolves it ONCE, at register time, so the id can be STORED and
    the read becomes a plain FK join.

    `*names` IS ORDERED, and the order is the caller's priority. MEASURED, and
    this is why: for THIS session the channel is `local_pc` (-> `Local PC`,
    environment_id=2, `is_active=0`) while the IDE is `VS Code` (-> `VS Code`,
    environment_id=6, `is_active=1`). The IDE is the more specific fact — a chat
    happens IN an IDE, and the channel is only where it was filed — so the caller
    passes the IDE first and the channel second.

    IT REFUSES A NEAR MATCH. The name must equal `working_environment.product`
    EXACTLY (case-insensitive, whitespace-trimmed). No substring, no prefix, no
    similarity score. When no candidate matches exactly it returns `ok=False`
    with the reason, and the caller stores NULL — an honest "not linked" beats a
    lucky join.

    `is_active` IS REPORTED, NOT REQUIRED. MEASURED: `Local PC` exists with
    `is_active=0`, so requiring `is_active=1` would refuse a row that IS the
    exact match. The flag is returned so the caller can see it; it does not
    silently disqualify the only exact answer.
    """
    tried: list[str] = []
    for raw in names:
        ch = str(raw or "").strip()
        if not ch:
            continue
        tried.append(ch)
        # A channel_key is resolved through channel_registry; a product name is
        # used as-is. Both are EXACT lookups.
        row = conn.execute(
            "SELECT name FROM channel_registry WHERE channel_key = ? OR name = ?",
            (ch, ch),
        ).fetchone()
        name = ch
        if row:
            name = str(row["name"] if not isinstance(row, tuple) else row[0]).strip()
        env = conn.execute(
            "SELECT environment_id, kind, product, surface, display, is_active "
            "FROM working_environment "
            "WHERE LOWER(TRIM(product)) = LOWER(TRIM(?)) "
            "ORDER BY is_active DESC, environment_id LIMIT 1",
            (name,),
        ).fetchone()
        if env:
            d = dict(env)
            d["ok"] = True
            d["matched_on"] = ch
            d["channel_name"] = name
            d["tried"] = tried
            return d
    return {
        "ok": False,
        "error_code": "NO_EXACT_ENVIRONMENT",
        "error": ("none of %r has an EXACT match in working_environment.product; "
                  "refusing a near match" % (tried,)),
        "tried": tried,
    }


# ---- user_environment: DB-driven who/where (user + IP + computer + locale) ----

def upsert_user_environment(
    *,
    user_id: str | None = None,
    ip_address: str | None = None,
    computer_id: str | None = None,
    computer_name: str | None = None,
    os_name: str | None = None,
    os_version: str | None = None,
    python_version: str | None = None,
    timezone_name: str | None = None,
    tz_offset_sec: int | None = None,
    country: str | None = None,
    country_code: str | None = None,
    region: str | None = None,
    city: str | None = None,
    isp: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    detail: str | None = None,
    source: str = "api",
) -> dict[str, Any]:
    """Insert or update one user_environment row (UNIQUE computer_id+ip_address).

    Returns {ok, id, action, row}.
    """
    cid = (computer_id or "").strip() or None
    ip = (ip_address or "").strip() or None
    conn = _conn()
    try:
        existing = None
        if cid or ip:
            existing = conn.execute(
                """
                SELECT id FROM user_environment
                WHERE computer_id IS ? AND ip_address IS ?
                """,
                (cid, ip),
            ).fetchone()
        if existing:
            rid = int(existing[0])
            conn.execute(
                """
                UPDATE user_environment
                SET user_id=?, computer_name=?, os_name=?, os_version=?,
                    python_version=?, timezone=?, tz_offset_sec=?,
                    country=?, country_code=?, region=?, city=?, isp=?,
                    language=?, locale=?, detail=?, source=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    user_id, computer_name, os_name, os_version,
                    python_version, timezone_name, tz_offset_sec,
                    country, country_code, region, city, isp,
                    language, locale, detail, source, rid,
                ),
            )
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO user_environment
                    (user_id, ip_address, computer_id, computer_name, os_name,
                     os_version, python_version, timezone, tz_offset_sec,
                     country, country_code, region, city, isp, language,
                     locale, detail, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, ip, cid, computer_name, os_name, os_version,
                    python_version, timezone_name, tz_offset_sec,
                    country, country_code, region, city, isp, language,
                    locale, detail, source,
                ),
            )
            rid = int(cur.lastrowid)
            action = "inserted"
        conn.commit()
        row = conn.execute(
            "SELECT * FROM user_environment WHERE id=?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    return {
        "ok": True,
        "id": rid,
        "action": action,
        "row": dict(row) if row else None,
    }


def list_user_environments(limit: int = 50) -> dict[str, Any]:
    """Recent user_environment rows (newest first) for the UI table."""
    lim = max(1, min(int(limit or 50), 500))
    conn = _conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, user_id, ip_address, computer_id, computer_name,
                       os_name, os_version, timezone, tz_offset_sec,
                       country, country_code, region, city, isp,
                       language, locale, detail, source,
                       created_at, updated_at
                FROM user_environment
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        ]
        total = conn.execute(
            "SELECT COUNT(*) FROM user_environment"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows), "total": int(total)}


# ---- pinned sessions: the VS Code pinned area, one row per session ----
#
# WHY THIS EXISTS (the human, 2026-09-27):
#   "workspace exclusive — i have work for that!! but why i can't find at my
#    http://127.0.0.1:18765/llm-tasks/user_environment/ ????"
#   "example VScode > chat > session"
#   "1) how many session at the pinned area, sample = 5
#    2) register to table for each session? 1 name = 1, 2 name = 2 ...
#    3) did have x,y for all session at pinned? sample = 1, x1,x2 and y1,y2 ..."
#
# THE ANSWER TO "why can't I find it": the work EXISTS and the page has NO READER
# for it. MEASURED 2026-09-27:
#   * `coordinate_session` holds 15 rows across 7 sessions — the session->target
#     link is real.
#   * `vscode_pinned_first_row` is a MEASURED band: (1285,285)-(1912,367).
#   * `_pinned_rows()` measures the row count at runtime and records it as
#     `pinned_rows_seen` in the evidence JSON.
#   * The page has 4 tabs (detect/presence/list/assets) and `grep 'pinned'` over
#     `llm_task_monitor_ui/src/app.js` returns ZERO matches.
# So the data is reachable and the browser cannot show it — the same defect class
# as `plan_SESSION.LIST.IS.THE.MEASURE.KEY`.
#
# THE GEOMETRY IS DERIVED, NOT INVENTED. The human: "each session height is fixed
# and width will update according to the session area" (quoted in
# `_proof_vscode_session_area.py`). So row n's rect is the band's x-range at
# `y1 + n*height .. y1 + (n+1)*height`. The band is the MEASURED part; the height
# is the human's stated rule. Nothing here is a guess.

# The band's row height, in px. MEASURED, not invented: the active
# `vscode_pinned_first_row` band (environment_template id 291) is
# `(1285,285)-(1912,367)`, i.e. **82px tall** — one pinned row is a TITLE line
# plus its metadata line, so the row is 82px, not 41px.
#
# MEASURED 2026-09-27, and this is why the first value was wrong: the earlier
# `41` was half a row, so `_rows_for_count(6, 5, band, 41)` drew 5 rows inside
# the space of 2.5 real rows. The human's rule ("each session height is fixed")
# is still the rule; the NUMBER is now the measured band height.
PINNED_ROW_HEIGHT = 82

# The step whose run RECORDS the pinned row count. `_pinned_rows()` measures it
# live and the runner writes it into `playwright_step_run.got` as
# `Pinned area measured [4, 1, 4] row(s) over 3 cycle(s)`.
PINNED_STEP_KEY = "open_pinned_session"


def _parse_pinned_rows(got: str) -> dict[str, Any]:
    """Read the row count OUT of a recorded step result.

    THE MEASUREMENT IS THE RUN'S, NOT OURS. MEASURED 2026-09-27: the count is
    `[4, 1, 4]` in the newest run, `[5]` in the last PASS, `[6]` before that —
    it CHANGES, because it is a live screen measurement. So this PARSES the
    recorded number rather than recomputing one.

    Returns `{seen, cycles, latest, max_seen, why}`. `seen` is the list of counts
    across the retry cycles; `latest` is the LAST cycle's count, which is the one
    the run acted on.
    """
    import re as _re
    m = _re.search(r"measured \[([^\]]+)\]\s*row\(s\)", str(got or ""))
    if not m:
        return {"seen": [], "cycles": 0, "latest": None, "max_seen": None,
                "why": "the recorded result carries no `measured [...] row(s)`"}
    try:
        seen = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
    except ValueError:
        return {"seen": [], "cycles": 0, "latest": None, "max_seen": None,
                "why": "the recorded counts are not integers: %r" % m.group(1)}
    cyc = _re.search(r"over (\d+) cycle", str(got or ""))
    return {
        "seen": seen,
        "cycles": int(cyc.group(1)) if cyc else len(seen),
        "latest": seen[-1] if seen else None,
        "max_seen": max(seen) if seen else None,
        "why": "",
    }


def _rows_for_count(
    environment_id: int,
    count: int,
    band: dict[str, Any] | None,
    row_height: int = PINNED_ROW_HEIGHT,
) -> list[dict[str, Any]]:
    """Build `count` rows from a band. Used when the COUNT came from elsewhere.

    WHY THIS IS SEPARATE FROM `list_pinned_sessions`. The count and the geometry
    have DIFFERENT sources: the count is a MEASUREMENT (a run's record, or a live
    screenshot), while the rect is DERIVED from the band. Keeping them in one
    function forced the count to come from the same query as the rows, which is
    how the first version ended up counting a TABLE. This takes the count as an
    ARGUMENT, so the caller decides where it came from.
    """
    if not band or count is None:
        return []
    conn = _conn()
    try:
        linked = conn.execute(
            "SELECT cs.session_id, MAX(cs.updated_at) AS last_seen, "
            "       COUNT(*) AS coord_count, "
            "       (SELECT COUNT(*) FROM identity_registry ir "
            "         WHERE ir.session_id = cs.session_id) AS identity_rows "
            "FROM coordinate_session cs WHERE cs.is_active = 1 "
            "GROUP BY cs.session_id "
            "ORDER BY MAX(cs.updated_at) DESC, cs.session_id").fetchall()
        out: list[dict[str, Any]] = []
        for i in range(int(count)):
            d = dict(linked[i]) if i < len(linked) else {}
            y1 = int(band["y1"]) + i * int(row_height)
            y2 = y1 + int(row_height)
            sid = d.get("session_id") or ""
            # THE SAME TWO FIELDS AS `list_pinned_sessions` (2026-09-27).
            # MEASURED, and this was a real defect: the LIVE path builds its rows
            # HERE, so adding `is_live` / `environment` only to
            # `list_pinned_sessions` left the live page showing `underivable` for
            # every row while the recorded path showed `VS Code`. ONE row shape,
            # built in ONE place, or the two paths disagree.
            live_info = _session_liveness(sid)
            env_info = _session_environment(conn, sid)
            out.append({
                "pinned_row_no": i + 1,
                "session_id": sid,
                "x1": int(band["x1"]), "y1": y1,
                "x2": int(band["x2"]), "y2": y2,
                "cx": (int(band["x1"]) + int(band["x2"])) // 2,
                "cy": (y1 + y2) // 2,
                "coord_count": int(d.get("coord_count") or 0),
                "identity_known": bool(d.get("identity_rows")),
                "last_seen": d.get("last_seen"),
                "is_live": live_info["is_live"],
                "age_sec": live_info["age_sec"],
                "status": "LIVE" if live_info["is_live"] else "idle",
                "status_why": live_info["why"],
                "environment_id": env_info["environment_id"],
                "environment": env_info["environment"],
                "environment_why": env_info["why"],
                "cite_ref": ("row %d of %d; rect derived from the band + "
                             "row_height=%d"
                             % (i + 1, int(count), int(row_height))),
            })
        return out
    finally:
        conn.close()


def list_pinned_sessions(
    environment_id: int = 6,
    *,
    row_height: int = PINNED_ROW_HEIGHT,
) -> dict[str, Any]:
    """The pinned session area: how many rows, and each row's rect.

    Returns `{ok, environment_id, count, source, measured_at, band, sessions, why}`.

    THE COUNT IS A MEASUREMENT, NOT A COUNT OF A TABLE.
    ---------------------------------------------------
    MEASURED 2026-09-27, and this is a defect I shipped and then fixed. The first
    version answered **7** by running
    `SELECT COUNT(DISTINCT session_id) FROM coordinate_session`. The human:
    *"7 個 session 喺 pinned area??? only have 5 now... seems you hardcode value
    not value by measure"*. **The human was right.**

    `coordinate_session` counts *sessions that have ever been linked to a
    coordinate*. The pinned area is *rows on screen right now*. Those are two
    different populations, and the number 7 was about the wrong one. The REAL
    measurement lives in `playwright_step_run.got`, written by `_pinned_rows()`:
    `[4, 1, 4]` in the newest run, `[5]` in the last PASS, `[6]` before that.

    So this reader PARSES the recorded measurement. It never recomputes a count
    from a table, because a count of a table is not a count of a list.

    NEVER returns a silent empty list. When no run has measured the pinned area
    the answer is `ok: False` with a `why`, because an empty list and a missing
    measurement have the SAME shape.
    """
    conn = _conn()
    try:
        # ---- THE MEASUREMENT: the newest run that recorded a pinned count ----
        # SCOPED BY ENVIRONMENT. `playwright_step_run.playwright_id` is a
        # `playwright_environment.id`, and THAT row carries the `environment_id`.
        # MEASURED 2026-09-27: without this join, `environment_id=999999` returned
        # the VS Code run's count — a number about the WRONG environment, which is
        # the same wrong-population defect this reader exists to avoid.
        run = conn.execute(
            "SELECT r.id, r.evidence_id, r.status, r.got, r.created_at, "
            "       r.image_name, pe.environment_id "
            "FROM playwright_step_run r "
            "JOIN playwright_environment pe ON pe.id = r.playwright_id "
            "WHERE r.step_key = ? AND pe.environment_id = ? "
            "AND r.got LIKE '%measured [%row(s)%' "
            "ORDER BY r.id DESC LIMIT 1",
            (PINNED_STEP_KEY, int(environment_id))).fetchone()
        if not run:
            return {
                "ok": False, "environment_id": int(environment_id),
                "count": None, "source": "playwright_step_run",
                "measured_at": None, "band": None, "sessions": [],
                "why": ("no `%s` run has ever recorded a pinned row count for "
                        "environment_id=%d — the pinned area has not been "
                        "measured there, so any number here would be a guess"
                        % (PINNED_STEP_KEY, int(environment_id))),
            }
        r = dict(run)
        parsed = _parse_pinned_rows(r["got"])
        if parsed["latest"] is None:
            return {
                "ok": False, "environment_id": int(environment_id),
                "count": None, "source": "playwright_step_run",
                "measured_at": r["created_at"], "band": None, "sessions": [],
                "why": parsed["why"],
            }
        count = int(parsed["latest"])

        # ---- THE BAND: the newest run's own rect, else the stored one ----
        # The stored `environment_template` row was collected 2026-09-26 02:43 and
        # the newest run puts the first PIN at y=482/505, so the stored band is
        # STALE. The run's own click point is the fresher evidence, so it is
        # preferred and the age of whatever is used is REPORTED.
        import re as _re
        click = _re.search(r"at \((\d+),(\d+)\)", str(r["got"] or ""))
        stored = conn.execute(
            "SELECT e.x1, e.y1, e.x2, e.y2, e.cx, e.cy, e.cite_ref, "
            "       e.collected_at "
            "FROM environment_template e "
            "JOIN target_template t ON t.id = e.template_id "
            "WHERE t.name = 'vscode_pinned_first_row' "
            "AND e.environment_id = ? AND e.is_active = 1",
            (int(environment_id),)).fetchone()
        band: dict[str, Any] | None = None
        band_source = ""
        if click:
            cx, cy = int(click.group(1)), int(click.group(2))
            x1 = int(stored["x1"]) if stored else cx - 300
            x2 = int(stored["x2"]) if stored else cx + 300
            band = {"x1": x1, "y1": cy - int(row_height) // 2,
                    "x2": x2, "y2": cy + int(row_height) // 2,
                    "cx": cx, "cy": cy,
                    "cite_ref": ("the newest `%s` run clicked the first PINNED "
                                 "row at (%d,%d) — evidence %s"
                                 % (PINNED_STEP_KEY, cx, cy, r["evidence_id"]))}
            band_source = "the run's own click point (fresher than the stored band)"
        elif stored:
            band = {"x1": int(stored["x1"]), "y1": int(stored["y1"]),
                    "x2": int(stored["x2"]), "y2": int(stored["y2"]),
                    "cx": stored["cx"], "cy": stored["cy"],
                    "cite_ref": stored["cite_ref"]}
            band_source = ("the stored `vscode_pinned_first_row` row, collected "
                           "%s — STALE if the panel has moved"
                           % (stored["collected_at"] or "NA"))

        # ---- THE ROWS: one per measured row, with a derived rect ----
        # The COUNT is the measurement. The session ids are the CONTEXT: which
        # sessions are linked to a coordinate, newest first. A row with no
        # session id is still a row — the count does not depend on the link.
        linked = conn.execute(
            "SELECT cs.session_id, MAX(cs.updated_at) AS last_seen, "
            "       COUNT(*) AS coord_count, "
            "       (SELECT COUNT(*) FROM identity_registry ir "
            "         WHERE ir.session_id = cs.session_id) AS identity_rows "
            "FROM coordinate_session cs WHERE cs.is_active = 1 "
            "GROUP BY cs.session_id "
            "ORDER BY MAX(cs.updated_at) DESC, cs.session_id").fetchall()
        sessions: list[dict[str, Any]] = []
        for i in range(count):
            d = dict(linked[i]) if i < len(linked) else {}
            y1 = int(band["y1"]) + i * int(row_height)
            y2 = y1 + int(row_height)
            sid = d.get("session_id") or ""
            # ---- IS THIS SESSION LIVE? (2026-09-27) --------------------------
            # THE HUMAN: "session status? is_live or not".
            #
            # MEASURED, and this is the signal that ALREADY EXISTS: a session's
            # own `chatSessions/<session-id>.jsonl` mtime, compared against
            # `LIVE_WINDOW_S` (300s). `/api/mode/sessions` computes exactly this
            # (`mouse_spot_helper.py:10722`). It is REUSED, never re-declared —
            # a second window would let the two pages disagree about "live".
            #
            # AND `coordinate_session.last_seen` IS NOT LIVENESS. MEASURED: it is
            # 16–27 HOURS old for every row, because it records when a coordinate
            # was last LINKED, not when the session ran. Using it would report
            # every session dead.
            live_info = _session_liveness(sid)
            # ---- WHICH ENVIRONMENT? (2026-09-27) -----------------------------
            # THE HUMAN: "session is worked under environment". MEASURED:
            # `identity_registry.environment_id` is NULL for 63 of 64 rows, so
            # the environment is DERIVED from `chat_main.ide` ->
            # `working_environment.product`. An underivable one is REPORTED.
            env_info = _session_environment(conn, sid)
            sessions.append({
                "pinned_row_no": i + 1,
                "session_id": sid,
                "x1": int(band["x1"]), "y1": y1,
                "x2": int(band["x2"]), "y2": y2,
                "cx": (int(band["x1"]) + int(band["x2"])) // 2,
                "cy": (y1 + y2) // 2,
                "coord_count": int(d.get("coord_count") or 0),
                "identity_known": bool(d.get("identity_rows")),
                "last_seen": d.get("last_seen"),
                # THE STATUS. `is_live` is a MEASUREMENT of the session's own
                # file, never a guess from a link time.
                "is_live": live_info["is_live"],
                "age_sec": live_info["age_sec"],
                "status": "LIVE" if live_info["is_live"] else "idle",
                "status_why": live_info["why"],
                # THE ENVIRONMENT. Derived, or REPORTED as underivable.
                "environment_id": env_info["environment_id"],
                "environment": env_info["environment"],
                "environment_why": env_info["why"],
                "cite_ref": ("row %d of %d MEASURED by `%s` (evidence %s); "
                             "rect derived from the band + row_height=%d"
                             % (i + 1, count, PINNED_STEP_KEY,
                                r["evidence_id"], int(row_height))),
            })
        return {
            "ok": True,
            "environment_id": int(environment_id),
            "count": count,
            "source": "playwright_step_run.got (the run's own measurement)",
            "measured_at": r["created_at"],
            "evidence_id": r["evidence_id"],
            "run_status": r["status"],
            "seen_across_cycles": parsed["seen"],
            "cycles": parsed["cycles"],
            "max_seen": parsed["max_seen"],
            "row_height": int(row_height),
            "band": band,
            "band_source": band_source,
            "linked_sessions": len(linked),
            "sessions": sessions,
            # THE STATUS SUMMARY, so the page can say how many are live without
            # re-deriving it. `live_window_s` is the SAME constant the mode page
            # uses.
            "live_window_s": _live_window_s(),
            "live_count": sum(1 for s in sessions if s.get("is_live")),
            "why": "",
        }
    finally:
        conn.close()


def _live_window_s() -> int:
    """The ONE liveness window. REUSED from `mouse_spot_helper.LIVE_WINDOW_S`.

    A second constant here would let this page and the mode page disagree about
    what "live" means. The import is deferred because `mouse_spot_helper` imports
    this module.
    """
    try:
        import mouse_spot_helper as _m
        return int(_m.LIVE_WINDOW_S)
    except Exception:
        return 300


def _session_liveness(session_id: str) -> dict[str, Any]:
    """Is this session LIVE? Read from its OWN `chatSessions/<id>.jsonl` mtime.

    Never raises. An unknown file is `is_live: False` with a NAMED reason — a
    session whose file cannot be found is not evidence of liveness.
    """
    out = {"is_live": False, "age_sec": None, "why": ""}
    sid = str(session_id or "").strip()
    if not sid:
        out["why"] = "no session_id on this row, so liveness cannot be measured"
        return out
    try:
        import os as _os
        import time as _time
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR / "scripts"))
        import mode_attest as _ma
        db, _wj = _ma.find_workspace_db()
        if not db:
            out["why"] = "no VS Code workspaceStorage entry matches this workspace"
            return out
        p = _os.path.join(_os.path.dirname(db), _ma.SESSIONS_DIRNAME,
                          sid + ".jsonl")
        if not _os.path.exists(p):
            out["why"] = ("no chatSessions/%s.jsonl — the session has no file, "
                          "so it is not running a turn" % sid)
            return out
        age = _time.time() - _os.path.getmtime(p)
        win = _live_window_s()
        out["age_sec"] = int(age)
        out["is_live"] = age < win
        out["why"] = ("the session's own file was touched %ds ago; the window is "
                      "%ds" % (int(age), win))
    except Exception as e:
        out["why"] = "liveness could not be measured (%s: %s)" % (
            type(e).__name__, e)
    return out


def _session_environment(conn: sqlite3.Connection,
                         session_id: str) -> dict[str, Any]:
    """Which environment does this session work under? DERIVED, never invented.

    TWO derivation paths, tried in order, and the one that answered is NAMED:

      1. `coordinate_session -> environment_template.environment_id` — the
         coordinate the session was LINKED to carries its environment. MEASURED:
         all 4 pinned sessions resolve to environment_id 6 (VS Code).
      2. `chat_main.ide -> working_environment.product` — the fallback.

    MEASURED: `identity_registry.environment_id` is NULL for 63 of 64 rows, so
    neither path can use it. An underivable one is REPORTED with a named reason.
    """
    out = {"environment_id": None, "environment": "", "why": ""}
    sid = str(session_id or "").strip()
    if not sid:
        out["why"] = "no session_id on this row"
        return out
    try:
        # ---- PATH 1: the coordinate the session was linked to ----------------
        row = conn.execute(
            "SELECT et.environment_id, we.product "
            "FROM coordinate_session cs "
            "JOIN environment_template et ON et.template_id = cs.coordinate_id "
            "LEFT JOIN working_environment we "
            "       ON we.environment_id = et.environment_id "
            "WHERE cs.session_id = ? AND cs.is_active = 1 "
            "  AND et.environment_id IS NOT NULL "
            "ORDER BY cs.updated_at DESC LIMIT 1", (sid,)).fetchone()
        if row and row["environment_id"] is not None:
            out["environment_id"] = int(row["environment_id"])
            out["environment"] = str(row["product"] or "")
            out["why"] = ("derived from the coordinate this session was linked "
                          "to (environment_template.environment_id=%d)"
                          % int(row["environment_id"]))
            return out
        # ---- PATH 2: the IDE the chat row names ------------------------------
        row = conn.execute(
            "SELECT ide FROM chat_main WHERE session_id = ? "
            "ORDER BY id LIMIT 1", (sid,)).fetchone()
        ide = str((row["ide"] if row else "") or "").strip()
        if not ide:
            out["why"] = ("no coordinate link and no chat_main row names an IDE "
                          "for this session, so the environment cannot be derived")
            return out
        env = conn.execute(
            "SELECT environment_id, product FROM working_environment "
            "WHERE product = ? AND is_active = 1 ORDER BY environment_id LIMIT 1",
            (ide,)).fetchone()
        if not env:
            out["why"] = ("no active working_environment row has product=%r, so "
                          "the environment cannot be derived" % ide)
            return out
        out["environment_id"] = int(env["environment_id"])
        out["environment"] = str(env["product"])
        out["why"] = "derived from chat_main.ide=%r" % ide
    except Exception as e:
        out["why"] = "environment could not be derived (%s: %s)" % (
            type(e).__name__, e)
    return out


# ---- app catalog + user_asset (DB-driven, id only) ----

def list_apps(active_only: bool = True) -> dict[str, Any]:
    """The app catalog (installed software)."""
    conn = _conn()
    try:
        where = "WHERE is_active = 1" if active_only else ""
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT app_id, app_key, name, kind, exe_path, process_name,
                       mcp_url, description, is_active, created_at, updated_at
                FROM app {where}
                ORDER BY name
                """
            ).fetchall()
        ]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows)}


def upsert_app(
    *,
    app_key: str,
    name: str,
    kind: str = "desktop",
    exe_path: str | None = None,
    process_name: str | None = None,
    mcp_url: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Insert or update one app row (UNIQUE app_key)."""
    key = (app_key or "").strip()
    if not key:
        return {"ok": False, "error": "app_key is required"}
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT app_id FROM app WHERE app_key = ?", (key,)
        ).fetchone()
        if row:
            aid = int(row["app_id"])
            conn.execute(
                """
                UPDATE app
                SET name=?, kind=?, exe_path=?, process_name=?, mcp_url=?,
                    description=?, updated_at=CURRENT_TIMESTAMP
                WHERE app_id=?
                """,
                (name, kind, exe_path, process_name, mcp_url, description, aid),
            )
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO app
                    (app_key, name, kind, exe_path, process_name, mcp_url, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key, name, kind, exe_path, process_name, mcp_url, description),
            )
            aid = int(cur.lastrowid)
            action = "inserted"
        conn.commit()
        out = conn.execute(
            "SELECT * FROM app WHERE app_id=?", (aid,)
        ).fetchone()
    finally:
        conn.close()
    return {"ok": True, "action": action, "app_id": aid,
            "row": dict(out) if out else None}


# ---- source catalog (Chat Center Setting "from" dropdown) ----

def list_sources(active_only: bool = True) -> dict[str, Any]:
    """The source catalog (IDE / APP / BROWSER a chat can come from).

    `instruction_limit` is the character limit of that source's custom-
    instruction box. NULL means NOT MEASURED — never "unlimited". A caller that
    treats NULL as unlimited would paste an over-long block and have it
    silently truncated by the app.
    """
    conn = _conn()
    try:
        where = "WHERE is_active = 1" if active_only else ""
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT id, source_key, name, kind, url, hotkey, description,
                       source_kind, app_id, instruction_limit,
                       is_active, created_at, updated_at
                FROM source {where}
                ORDER BY id
                """
            ).fetchall()
        ]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows)}


def upsert_source(
    *,
    source_key: str,
    name: str,
    kind: str = "APP",
    url: str | None = None,
    hotkey: str | None = None,
    description: str | None = None,
    instruction_limit: int | None = None,
    clear_instruction_limit: bool = False,
) -> dict[str, Any]:
    """Insert or update one source row (UNIQUE source_key).

    `instruction_limit` is the MEASURED character limit of that source's
    custom-instruction box. NULL means NOT MEASURED — never "unlimited".

    Passing `instruction_limit=None` LEAVES the stored value alone (so a caller
    updating only the name does not wipe a measurement). To deliberately reset a
    measurement back to "not measured", pass `clear_instruction_limit=True` —
    without that flag there would be no way to un-measure a wrong number.
    """
    key = (source_key or "").strip()
    if not key:
        return {"ok": False, "error": "source_key is required"}
    k = (kind or "APP").strip().upper()
    if k not in ("IDE", "APP", "BROWSER"):
        return {"ok": False, "error": "kind must be IDE, APP or BROWSER"}
    if instruction_limit is not None:
        try:
            instruction_limit = int(instruction_limit)
        except (TypeError, ValueError):
            return {"ok": False, "error": "instruction_limit must be an integer"}
        if instruction_limit <= 0:
            return {"ok": False, "error": "instruction_limit must be positive"}
    if clear_instruction_limit:
        instruction_limit = None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM source WHERE source_key = ?", (key,)
        ).fetchone()
        if row:
            sid = int(row["id"])
            if clear_instruction_limit:
                conn.execute(
                    """
                    UPDATE source
                    SET name=?, kind=?, url=?, hotkey=?, description=?,
                        instruction_limit=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (name, k, url, hotkey, description, sid),
                )
            else:
                conn.execute(
                    """
                    UPDATE source
                    SET name=?, kind=?, url=?, hotkey=?, description=?,
                        instruction_limit=COALESCE(?, instruction_limit),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (name, k, url, hotkey, description, instruction_limit, sid),
                )
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO source
                    (source_key, name, kind, url, hotkey, description,
                     instruction_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key, name, k, url, hotkey, description, instruction_limit),
            )
            sid = int(cur.lastrowid)
            action = "inserted"
        conn.commit()
        out = conn.execute("SELECT * FROM source WHERE id=?", (sid,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "action": action, "id": sid,
            "row": dict(out) if out else None}


# ---- flow_setting (Chat Center flow table) ----

def list_flow_settings(
    flow_key: str | None = None, active_only: bool = True
) -> dict[str, Any]:
    """Flow steps, ordered by flow_key then step_no."""
    conn = _conn()
    try:
        where: list[str] = []
        params: list[Any] = []
        if flow_key:
            where.append("flow_key = ?")
            params.append(str(flow_key))
        if active_only:
            where.append("is_active = 1")
        sql = (
            "SELECT id, flow_key, step_no, question, value, next_step, action, "
            "description, is_active, created_at, updated_at FROM flow_setting"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY flow_key, step_no"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows)}


def upsert_flow_setting(
    *,
    flow_key: str,
    step_no: int,
    question: str | None = None,
    value: str | None = None,
    next_step: int | None = None,
    action: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Insert or update one flow step (UNIQUE flow_key + step_no)."""
    key = (flow_key or "").strip()
    if not key:
        return {"ok": False, "error": "flow_key is required"}
    try:
        step = int(step_no)
    except (TypeError, ValueError):
        return {"ok": False, "error": "step_no must be an integer"}
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM flow_setting WHERE flow_key = ? AND step_no = ?",
            (key, step),
        ).fetchone()
        if row:
            fid = int(row["id"])
            conn.execute(
                """
                UPDATE flow_setting
                SET question=?, value=?, next_step=?, action=?, description=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (question, value, next_step, action, description, fid),
            )
            action_taken = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO flow_setting
                    (flow_key, step_no, question, value, next_step, action, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key, step, question, value, next_step, action, description),
            )
            fid = int(cur.lastrowid)
            action_taken = "inserted"
        conn.commit()
        out = conn.execute(
            "SELECT * FROM flow_setting WHERE id=?", (fid,)
        ).fetchone()
    finally:
        conn.close()
    return {"ok": True, "action": action_taken, "id": fid,
            "row": dict(out) if out else None}


def delete_flow_setting(flow_id: int) -> dict[str, Any]:
    """Delete one flow step by id."""
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM flow_setting WHERE id=?", (int(flow_id),))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "not found"}
    finally:
        conn.close()
    return {"ok": True, "deleted": int(flow_id)}


# ---- flow execution (the `action` column, wired) ----
# The flow table's `action` was a LABEL only. This resolves it to a real
# operation so clicking a step in the UI can actually run it.
#
# WHY the action is resolved here and not in the UI: the UI must not know how a
# template is pasted (that is the tool layer's job), and the same flow must be
# replayable by a script later. So the mapping lives next to the data.
FLOW_ACTIONS: dict[str, dict[str, Any]] = {
    # Paste a format_templates row into the focused chat input. The paste itself
    # is done by the existing tool chain (f_new_session.py writes the template
    # text file; f_env_prep.py --prep focuses the box and pastes it).
    "paste_template": {
        "kind": "template_paste",
        "tool": "f_env_prep.py --prep",
        "description": "Paste the step's template into the focused chat input.",
    },
    # Ask the model to echo the identity block back (CONFIRM YES/NO).
    "confirm_identity": {
        "kind": "template_paste",
        "tool": "f_env_prep.py --prep",
        "description": "Paste the confirm template and read the echoed block.",
    },
}


def resolve_flow_action(action: str | None) -> dict[str, Any]:
    """Map a flow step's `action` to a concrete operation.

    Returns {ok, action, kind, tool, description} or {ok: False, error} for an
    unknown action — an unknown action must be REFUSED, not silently ignored,
    because a step that looks runnable but does nothing is a false success.
    """
    key = str(action or "").strip()
    if not key:
        return {"ok": False, "error": "step has no action"}
    spec = FLOW_ACTIONS.get(key)
    if not spec:
        return {
            "ok": False,
            "error": "unknown action %r (known: %s)"
                     % (key, ", ".join(sorted(FLOW_ACTIONS))),
        }
    return {"ok": True, "action": key, **spec}


def get_flow_step(flow_key: str, step_no: int) -> dict[str, Any] | None:
    """One flow step by (flow_key, step_no), or None."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM flow_setting WHERE flow_key=? AND step_no=?",
            (str(flow_key), int(step_no)),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def run_flow_step(flow_key: str, step_no: int) -> dict[str, Any]:
    """Resolve one flow step into a runnable plan.

    This does NOT perform the UI action itself — it returns the resolved action
    plus the template text, so the caller (the tool layer / a human) can execute
    it. Keeping the resolution separate from the execution means the flow can be
    inspected and tested without moving the mouse.
    """
    step = get_flow_step(flow_key, step_no)
    if not step:
        return {"ok": False, "error": "step not found: %s #%s" % (flow_key, step_no)}
    resolved = resolve_flow_action(step.get("action"))
    if not resolved.get("ok"):
        return {"ok": False, "error": resolved.get("error"), "step": step}

    # The step's `value` names the template to paste.
    template_key = str(step.get("value") or "").strip()
    template: dict[str, Any] | None = None
    if template_key:
        try:
            import coord_store as cs

            row = cs.get_format_template_by_key(template_key)
            if row:
                template = {
                    "id": row.get("id"),
                    "prompt_setting_key": row.get("prompt_setting_key"),
                    "name": row.get("name"),
                    "mode": row.get("mode"),
                    "instruction": row.get("instruction"),
                }
        except Exception as e:
            return {
                "ok": False,
                "error": "template lookup failed: %s: %s" % (type(e).__name__, e),
                "step": step,
            }
        if template is None:
            return {
                "ok": False,
                "error": "template %r not found in format_templates" % template_key,
                "step": step,
            }

    return {
        "ok": True,
        "flow_key": step.get("flow_key"),
        "step_no": step.get("step_no"),
        "question": step.get("question"),
        "value": step.get("value"),
        "next_step": step.get("next_step"),
        "action": resolved.get("action"),
        "kind": resolved.get("kind"),
        "tool": resolved.get("tool"),
        "description": resolved.get("description"),
        "template": template,
    }


def list_user_assets(
    user_id: int | None = None, limit: int = 200
) -> dict[str, Any]:
    """user_asset rows joined to app + users, newest first."""
    lim = max(1, min(int(limit or 200), 1000))
    conn = _conn()
    try:
        where = "WHERE ua.user_id = ?" if user_id is not None else ""
        params: tuple[Any, ...] = (int(user_id), lim) if user_id is not None else (lim,)
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT ua.id, ua.user_id, u.name AS user_name,
                       ua.app_id, a.app_key, a.name AS app_name, a.kind,
                       ua.status, ua.version, ua.exe_path, ua.is_enabled,
                       ua.last_seen_at, ua.detail, ua.source,
                       ua.created_at, ua.updated_at
                FROM user_asset ua
                JOIN app a   ON a.app_id = ua.app_id
                LEFT JOIN users u ON u.user_id = ua.user_id
                {where}
                ORDER BY ua.updated_at DESC, ua.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
        total = conn.execute("SELECT COUNT(*) FROM user_asset").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows), "total": int(total)}


def upsert_user_asset(
    *,
    user_id: int,
    app_id: int,
    status: str = "unknown",
    version: str | None = None,
    exe_path: str | None = None,
    is_enabled: bool = True,
    detail: str | None = None,
    source: str = "sync",
) -> dict[str, Any]:
    """Insert or update one user_asset row (UNIQUE user_id+app_id)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM user_asset WHERE user_id=? AND app_id=?",
            (int(user_id), int(app_id)),
        ).fetchone()
        seen = "CURRENT_TIMESTAMP" if status == "online" else "last_seen_at"
        if row:
            rid = int(row["id"])
            conn.execute(
                f"""
                UPDATE user_asset
                SET status=?, version=?, exe_path=?, is_enabled=?, detail=?,
                    source=?, last_seen_at={seen},
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, version, exe_path, 1 if is_enabled else 0, detail,
                 source, rid),
            )
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO user_asset
                    (user_id, app_id, status, version, exe_path, is_enabled,
                     detail, source, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                        CASE WHEN ? = 'online' THEN CURRENT_TIMESTAMP ELSE NULL END)
                """,
                (int(user_id), int(app_id), status, version, exe_path,
                 1 if is_enabled else 0, detail, source, status),
            )
            rid = int(cur.lastrowid)
            action = "inserted"
        conn.commit()
        out = conn.execute(
            "SELECT * FROM user_asset WHERE id=?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    return {"ok": True, "action": action, "id": rid,
            "row": dict(out) if out else None}


def sync_user_assets(user_id: int, *, source: str = "sync") -> dict[str, Any]:
    """Refresh user_asset for one user from the live environment.

    For each app in the catalog, decide a status:
      - openclaw: online when its MCP port accepts a connection
      - ollama:   online when its HTTP port accepts a connection
      - others:   online when ITS OWN process is running, else installed

    MEASURED DEFECT THIS FIXES (2026-09-24)
    ---------------------------------------
    This function called `openclaw_settings.is_process_running()` ONCE, outside
    the loop, and compared that ONE result against every app's `process_name`:

        proc = ocs.is_process_running()          # an OPENCLAW-ONLY probe
        running = bool(proc.get("running")
                       and str(proc.get("name") or "").lower()
                       == str(a.get("process_name") or "").lower())

    `ocs.is_process_running()` searches ONLY `OPENCLAW_PROCESS_NAMES`, so the
    comparison could be true for `openclaw` and for NOTHING ELSE. MEASURED
    result: VS Code and Chrome were reported "process not running" while BOTH
    were running (20 and 17 instances).

    THE PROBE WAS NOT BROKEN -- IT WAS ANSWERING A DIFFERENT QUESTION. It
    answers "is OpenClaw running"; this caller read it as "is THIS app running".
    So each app is now probed by ITS OWN `process_name`, from ONE snapshot.
    """
    import openclaw_settings as ocs
    import process_probe as pp

    conn = _conn()
    try:
        apps = [
            dict(r)
            for r in conn.execute(
                "SELECT app_id, app_key, name, exe_path, process_name, mcp_url "
                "FROM app WHERE is_active = 1 ORDER BY name"
            ).fetchall()
        ]
    finally:
        conn.close()

    host, port = ocs._mcp_host_port()
    mcp_up = ocs.port_open(host, port)

    # ONE snapshot for every app: the counter query costs ~1s, so asking it once
    # per app would make a sync of N apps cost N seconds.
    names = [str(a.get("process_name") or "") for a in apps
             if str(a.get("process_name") or "").strip()]
    probes = pp.probe_all(names) if names else {"ok": False, "probes": {}}

    results: list[dict[str, Any]] = []
    for a in apps:
        key = str(a.get("app_key") or "")
        exe = a.get("exe_path")
        installed = bool(exe and Path(str(exe)).is_file())
        if key == "openclaw":
            status = "online" if mcp_up else ("installed" if installed else "missing")
            detail = (
                "MCP %s:%s reachable" % (host, port) if mcp_up
                else "MCP %s:%s closed" % (host, port)
            )
        elif key == "ollama":
            # Ollama is a service: probe its HTTP port, not a process name.
            up = ocs.port_open("127.0.0.1", 18803)
            status = "online" if up else ("installed" if installed else "missing")
            detail = "HTTP 127.0.0.1:18803 %s" % ("reachable" if up else "closed")
        else:
            # THIS app's OWN process name, from the shared snapshot. `online`
            # requires a MEASURED instance count > 0 -- never a default.
            p = probes.get("probes", {}).get(str(a.get("process_name") or ""), {})
            count = int(p.get("count") or 0)
            running = bool(p.get("ok")) and count > 0
            status = "online" if running else ("installed" if installed else "missing")
            detail = "process `%s` %s" % (
                a.get("process_name") or "-",
                ("running (%d instance(s))" % count) if running else "not running")
        out = upsert_user_asset(
            user_id=int(user_id),
            app_id=int(a["app_id"]),
            status=status,
            exe_path=str(exe) if exe else None,
            detail=detail,
            source=source,
        )
        results.append({
            "app_key": key, "app_name": a.get("name"),
            "status": status, "action": out.get("action"), "detail": detail,
        })
    return {
        "ok": True,
        "user_id": int(user_id),
        "synced": len(results),
        "online": sum(1 for r in results if r["status"] == "online"),
        "results": results,
    }


# ---- chat center: 3-step flow (submit -> analyze catalog/subcatalog/skill -> answer) ----

# Keyword -> catalog hints. Deterministic, no LLM (Phase A default).
_CATALOG_HINTS: list[tuple[str, str]] = [
    ("ui", "UI"),
    ("front-end", "UI"),
    ("frontend", "UI"),
    ("design", "UI"),
    ("db", "Database"),
    ("database", "Database"),
    ("sql", "Database"),
    ("table", "Database"),
    ("schema", "Database"),
    ("chat", "Chat"),
    ("message", "Chat"),
    ("prompt", "Chat"),
    ("llm", "Chat"),
    ("model", "Chat"),
    ("test", "QA"),
    ("qc", "QA"),
    ("verify", "QA"),
    ("bug", "QA"),
    ("api", "API"),
    ("endpoint", "API"),
    ("rest", "API"),
    ("http", "API"),
]

_SUBCATALOG_HINTS: list[tuple[str, str]] = [
    ("chat", "chat_center"),
    ("message", "chat_center"),
    ("ui", "ui_panel"),
    ("design", "ui_panel"),
    ("table", "schema"),
    ("schema", "schema"),
    ("sql", "schema"),
    ("api", "rest_api"),
    ("endpoint", "rest_api"),
    ("rest", "rest_api"),
    ("test", "verification"),
    ("verify", "verification"),
    ("qc", "verification"),
]


def _slugify_name(text: str) -> str:
    """Lowercase underscore slug for auto-created catalog/subcatalog names."""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "general"


def _match_catalog(conn: sqlite3.Connection, text: str) -> tuple[int, str]:
    """Match an existing catalog by keyword; create one if none matches."""
    low = (text or "").lower()
    rows = conn.execute("SELECT id, name FROM catalog").fetchall()
    existing = {str(r[1]).lower(): (int(r[0]), str(r[1])) for r in rows}
    # 1) keyword hint
    for frag, name in _CATALOG_HINTS:
        if frag in low:
            hit = existing.get(name.lower())
            if hit:
                return hit
            cur = conn.execute(
                "INSERT OR IGNORE INTO catalog (name, description) VALUES (?, ?)",
                (name, "auto-created by chat_center"),
            )
            if cur.lastrowid:
                return int(cur.lastrowid), name
            row = conn.execute(
                "SELECT id, name FROM catalog WHERE name=?", (name,)
            ).fetchone()
            return int(row[0]), str(row[1])
    # 2) direct name mention of an existing catalog
    for key, (cid, cname) in existing.items():
        if key and key in low:
            return cid, cname
    # 3) fallback: first catalog, else create "general"
    if rows:
        return int(rows[0][0]), str(rows[0][1])
    cur = conn.execute(
        "INSERT OR IGNORE INTO catalog (name, description) VALUES ('general', ?)",
        ("auto-created by chat_center",),
    )
    if cur.lastrowid:
        return int(cur.lastrowid), "general"
    row = conn.execute("SELECT id, name FROM catalog WHERE name='general'").fetchone()
    return int(row[0]), "general"


def _match_subcatalog(
    conn: sqlite3.Connection, catalog_id: int, text: str
) -> tuple[int, str]:
    """Match a subcatalog under catalog_id by keyword; create if missing."""
    low = (text or "").lower()
    rows = conn.execute(
        "SELECT id, name FROM subcatalog WHERE catalog_id=?", (catalog_id,)
    ).fetchall()
    existing = {str(r[1]).lower(): (int(r[0]), str(r[1])) for r in rows}
    for frag, name in _SUBCATALOG_HINTS:
        if frag in low:
            hit = existing.get(name.lower())
            if hit:
                return hit
            cur = conn.execute(
                "INSERT OR IGNORE INTO subcatalog (catalog_id, name, description) "
                "VALUES (?, ?, ?)",
                (catalog_id, name, "auto-created by chat_center"),
            )
            if cur.lastrowid:
                return int(cur.lastrowid), name
            row = conn.execute(
                "SELECT id, name FROM subcatalog WHERE catalog_id=? AND name=?",
                (catalog_id, name),
            ).fetchone()
            if row:
                return int(row[0]), str(row[1])
    for key, (sid, sname) in existing.items():
        if key and key in low:
            return sid, sname
    if rows:
        return int(rows[0][0]), str(rows[0][1])
    name = _slugify_name(text)[:40] or "general"
    cur = conn.execute(
        "INSERT OR IGNORE INTO subcatalog (catalog_id, name, description) "
        "VALUES (?, ?, ?)",
        (catalog_id, name, "auto-created by chat_center"),
    )
    if cur.lastrowid:
        return int(cur.lastrowid), name
    row = conn.execute(
        "SELECT id, name FROM subcatalog WHERE catalog_id=? ORDER BY id LIMIT 1",
        (catalog_id,),
    ).fetchone()
    return int(row[0]), str(row[1])


def _match_skill(
    conn: sqlite3.Connection, text: str
) -> tuple[str, str]:
    """Match an existing skill by name/description keyword. Returns (id, name).

    No auto-create: skills need a real definition + versions.
    """
    low = (text or "").lower()
    try:
        rows = conn.execute("SELECT skill_id, description FROM skills").fetchall()
    except sqlite3.Error:
        return "", ""
    # 1) skill_id substring in the text
    for r in rows:
        sid = str(r[0] or "")
        if sid and sid.lower() in low:
            return sid, sid
    # 2) token overlap on skill_id words
    tokens = {t for t in re.split(r"[^a-z0-9]+", low) if len(t) > 2}
    best: tuple[int, str, str] = (0, "", "")
    for r in rows:
        sid = str(r[0] or "")
        parts = {p for p in re.split(r"[^a-z0-9]+", sid.lower()) if len(p) > 2}
        score = len(tokens & parts)
        if score > best[0]:
            best = (score, sid, sid)
    if best[0] > 0:
        return best[1], best[2]
    return "", ""


def analyze_chat_content(text: str) -> dict[str, Any]:
    """STEP 1 analysis: match (or create) catalog / subcatalog / skill from text.

    Deterministic keyword matching against the DB — no LLM call.
    Returns {ok, catalog:{id,name}, subcatalog:{id,name}, skill:{id,name}, text}.
    """
    content = (text or "").strip()
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subcatalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                catalog_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                UNIQUE(catalog_id, name)
            )
            """
        )
        cat_id, cat_name = _match_catalog(conn, content)
        sub_id, sub_name = _match_subcatalog(conn, cat_id, content)
        skill_id, skill_name = _match_skill(conn, content)
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "text": content,
        "catalog": {"id": cat_id, "name": cat_name},
        "subcatalog": {"id": sub_id, "name": sub_name},
        "skill": {"id": skill_id, "name": skill_name},
    }


def create_chat_center_message(
    *,
    session_id: str | None = None,
    chat_id: int | None,
    sha256: str | None = None,
    chat_hash: str | None = None,
    role: str = "Question",
    content: str | None = None,
    title: str | None = None,
    event: str | None = None,
    evidence_ref: str | None = None,
    rule_version: str | None = None,
    measured_effect: str | None = None,
    status: str | None = None,
    catalog_id: int | None = None,
    catalog_name: str | None = None,
    subcatalog_id: int | None = None,
    subcatalog_name: str | None = None,
    skill_id: str | None = None,
    skill_name: str | None = None,
    llm: str | None = None,
    ide: str | None = None,
    answered_at: str | None = None,
    fault_ref: str | None = None,
) -> dict[str, Any]:
    """Append one chat_center_message row. Returns {ok, id, row}.

    chat_id is the INTEGER chat_main.id; sha256 is the content hash.

    title / event / evidence_ref / rule_version / measured_effect were added so
    a DISCOVERY can be stored as a first-class row: the table previously had
    `content` but no `title`, so a finding could be written and never listed or
    searched. `evidence_ref` carries citation discipline — a finding with no
    checkable reference (path:line or a command) is DISCARDED at this write
    site, never downgraded to a low-confidence finding.

    `fault_ref` is the `fault:<group>#<event_id>` this message REPORTS, in its OWN
    column. MEASURED why it is not `measured_effect`: the link lived there and an
    acknowledgment's NOTE was written into the same slot, so the ack overwrote the
    link (measured 2026-09-24). A note is written over its column; a link must
    survive being used.

    `status` is the WORKFLOW state of the turn (done | ask | progressive | QC).
    It is validated HERE rather than by a CHECK constraint, because SQLite
    cannot add a CHECK to an existing table without rebuilding it, and a
    rebuild of a live message log is a bigger risk than the value it buys. An
    unknown status is REFUSED with a reason, never stored as free text.
    """
    if sha256 is None and session_id:
        sha256 = sha256_from_session(session_id)
    # Workflow status: a closed vocabulary, refused at the write site.
    st = (status or "").strip()
    if st and st not in WORKFLOW_STATUSES:
        return {
            "ok": False,
            "error_code": "INVALID_STATUS",
            "error": ("status %r is not one of %s; an unknown workflow state "
                      "cannot be filtered or counted"
                      % (status, ", ".join(WORKFLOW_STATUSES))),
        }
    # Citation-discipline gate: a discovery/lesson MUST cite something.
    if (event or "").strip().lower() in ("discovery", "lesson"):
        if not (evidence_ref or "").strip():
            return {
                "ok": False,
                "error_code": "MISSING_EVIDENCE_REF",
                "error": ("event=%r requires a non-empty evidence_ref "
                          "(path:line or command); uncited findings are "
                          "discarded, not downgraded" % event),
            }
    # THE TITLE IS REQUIRED FOR A QUESTION (the human, 2026-09-27): "each chat
    # need to have title (requied) + content (requied) + entity ID (optional)".
    # MEASURED before this: `role='Question'` = 252 rows, **0 with a title**,
    # while all 488 titled rows were `role='Answer'` (watchdog fault reports).
    # A question with no title cannot be listed or searched, so it is refused
    # HERE rather than stored as NULL. `content` is already required in practice
    # (2537/2537 non-empty) and stays required.
    if str(role or "").strip() == "Question" and not str(title or "").strip():
        return {
            "ok": False,
            "error_code": "TITLE_REQUIRED",
            "error": ("a Question row requires a non-empty title: a question "
                      "with no title cannot be listed or searched, so it is a "
                      "row a reader cannot identify"),
        }
    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO chat_center_message
                (session_id, chat_id, sha256, chat_hash, role, content,
                 title, event, evidence_ref, rule_version, measured_effect,
                 catalog_id, catalog_name, subcatalog_id, subcatalog_name,
                 skill_id, skill_name, llm, ide, answered_at, status, fault_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, chat_id, sha256, chat_hash, role, content,
                title, event, evidence_ref, rule_version, measured_effect,
                catalog_id, catalog_name, subcatalog_id, subcatalog_name,
                skill_id, skill_name, llm, ide, answered_at, st or None,
                fault_ref or None,
            ),
        )
        conn.commit()
        rid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM chat_center_message WHERE id=?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    return {"ok": True, "id": rid, "row": dict(row) if row else None}


def list_chat_center_messages(
    *,
    chat_id: int | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """READ `chat_center_message` for a chat_id or a session_id. Read-only.

    WHY THIS EXISTS (MEASURED 2026-09-26). `/api/chat_center/history` called
    `sla.list_chat_center_messages(...)`, and a search for its definition found
    **nothing** — the function was never written. So the endpoint raised
    `AttributeError` and returned **HTTP 500**, and Chat Center's "Discovery
    List" tab showed an empty page, which reads as "no data" rather than "the
    reader is missing".

    THE SHAPE IS THE ENDPOINT'S OWN, not a new one: the route already called
    `(chat_id=..., session_id=..., limit=...)` and returned the result directly
    as JSON, so this returns `{ok, rows, total}` and the route keeps its
    `jsonify(...)`.

    A CALL WITH NEITHER KEY IS REFUSED. MEASURED: 348 of 2344 rows have no
    `chat_id`, so "no filter" would silently mix chats under a heading that
    names one. The refusal is returned as data (`ok: False`) so the caller can
    tell a bad request from a dead reader.
    """
    if chat_id is None and not (session_id or "").strip():
        return {"ok": False, "rows": [], "total": 0,
                "error_code": "MISSING_FILTER",
                "error": ("chat_id or session_id is required; an unfiltered "
                          "read would mix chats and 348 rows carry no chat_id")}
    try:
        lim = max(1, int(limit))
    except (TypeError, ValueError):
        lim = 50
    where, args = [], []
    if chat_id is not None:
        where.append("chat_id = ?")
        args.append(int(chat_id))
    if (session_id or "").strip():
        where.append("session_id = ?")
        args.append(str(session_id).strip())
    sql = ("SELECT * FROM chat_center_message WHERE " + " AND ".join(where)
           + " ORDER BY id DESC LIMIT ?")
    args.append(lim)
    conn = _conn()
    try:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "total": len(rows)}


def correct_chat_llm(
    session_id: str,
    *,
    alias: str | None = None,
    real_llm: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Correct the model for a session THROUGH THE REGISTER.

    WHY THIS EXISTS (the human, 2026-09-25):
        "session id is? llm is deepseek V4.1 Flas, non copilot"
        "get by my LLM table under identity table? / or you are fucking for
         handcode again"

    MEASURED: `chat_main` recorded `llm='copilot'` for session
    `50f58738-...`, while the register holds `DeepSeek V4.1 Flash 0731`. The
    recorded value was a value a CALLER typed, not a fact that was measured.

    THE DESIGN (plan_LLM.BY.REGISTER.NOT.BY.LOG §1.3)
    -------------------------------------------------
    This function used to write `chat_main.llm` DIRECTLY from a free-text
    `real_llm`. MEASURED 2026-09-25: it called neither `identity_llm` nor
    `resolve_alias`, so it was a SECOND writer that knew the rule — exactly the
    drift the plan forbids.

    It now DELEGATES to `identity_llm.assign_for_session_by_alias`, which:
      * resolves the alias to a REAL `llm_model.id` (EXACT, never fuzzy);
      * REFUSES an unknown alias, naming what IS registered;
      * REFUSES a session with no active identity;
      * writes `identity_registry.llm_id` — the FACT.

    `chat_main.llm` is then DERIVED from the register, so the display name and
    the fact cannot disagree (QC-08).

    `alias` is the provider's own model id (e.g. `deepseek/deepseek-v4.1-flash`).
    `real_llm` is accepted ONLY as a fallback for a caller that has a NAME and
    not an alias; it is resolved through the alias table too, and an unknown
    name is REFUSED rather than written.
    """
    sid = (session_id or "").strip()
    if not is_valid_session_id(sid):
        return {"ok": False, "error_code": "INVALID_SESSION_ID",
                "error": "session_id must be a canonical UUID (8-4-4-4-12)",
                "session_id": sid}
    want = str(alias or "").strip() or str(real_llm or "").strip()
    if not want:
        return {"ok": False, "error_code": "NO_MEASURED_MODEL",
                "error": ("neither alias nor real_llm was given; the model was "
                          "not measured, so there is nothing to correct TO")}
    import identity_llm as il
    # `_conn()` takes no argument, so a caller-supplied db_path is honoured by
    # opening the connection here. MEASURED: the first version called
    # `_conn(db_path)` and would have raised TypeError on every call.
    if db_path is None:
        conn = _conn()
    else:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
    try:
        # THE ONE WRITER. A refusal is returned, never softened.
        try:
            out = il.assign_for_session_by_alias(
                conn, session_id=sid, alias=want,
                cite_ref="skill_library_api.correct_chat_llm: %s" % want)
        except il.LlmRefused as exc:
            return {"ok": False, "error_code": "LLM_REFUSED",
                    "error": str(exc), "session_id": sid, "wanted": want}
        if not out.get("ok"):
            return {"ok": False,
                    "error_code": out.get("code") or "ASSIGN_REFUSED",
                    "error": out.get("message") or out.get("error"),
                    "session_id": sid, "wanted": want,
                    "known": out.get("known")}
        # DERIVE the display name from the register, so the two cannot disagree.
        name = str(out.get("llm_name") or "").strip()
        row = conn.execute(
            "SELECT id, llm FROM chat_main WHERE session_id=?", (sid,)).fetchone()
        if not row:
            return {"ok": False, "error_code": "NO_SUCH_CHAT",
                    "error": "no chat_main row for session %s" % sid,
                    "session_id": sid}
        before = str(row["llm"] or "")
        changed = before != name
        if changed:
            conn.execute(
                "UPDATE chat_main SET llm=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (name, int(row["id"])))
            conn.commit()
        return {"ok": True, "changed": changed, "session_id": sid,
                "chat_id": int(row["id"]), "before": before, "llm": name,
                "llm_id": out.get("llm_id"), "alias": out.get("alias"),
                "source": "identity_registry.llm_id (derived, not typed)"}
    finally:
        conn.close()


def register_conversation(
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    status: str = "draft",
    ide: str | None = None,
    llm: str | None = None,
    source: str = "chat_center",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Register a WHOLE conversation, ONE `chat_center_message` row per turn.

    WHY THIS EXISTS (the human, 2026-09-25):
        "get the chat ID and register my message at chat
         http://127.0.0.1:18765/llm-tasks/chat_identity/recent
         + status = draft
         so i can step to step to look into that, not but your fucking
         bullshit anymore"

    MEASURED BEFORE THIS: this session (`50f58738-...`) had **0** rows in
    `chat_main`, `chat_identity_log` AND `chat_center_message`. So the
    conversation the human was reading had no row anywhere, and the UI showed
    other chats and not this one.

    `turns` is a list of `{"role": "Question"|"Answer", "content": str}`. The
    rows are written IN ORDER, so the UI reads as a transcript rather than a bag.

    IT REUSES `create_chat_center_message` — the ONE writer of that table. A
    second writer would be a second place that knows the status rule, and the
    two would drift (the defect this module already records for
    `set_chat_message_status`).

    `status` defaults to `draft`: written down, NOT yet reviewed by the human.
    An unknown status is REFUSED by the writer, so this function cannot invent
    one.

    `conn` IS NOT PASSED DOWN. MEASURED: `register_chat_identity` and
    `create_chat_center_message` each open their OWN connection and commit, so
    neither accepts a `conn`. That is safe HERE because this function holds no
    uncommitted write of its own — it only reads. (The self-deadlock
    `set_chat_message_status` warns about needs an OPEN TRANSACTION, which this
    function never has.)
    """
    sid = (session_id or "").strip()
    if not is_valid_session_id(sid):
        return {"ok": False, "error_code": "INVALID_SESSION_ID",
                "error": "session_id must be a canonical UUID (8-4-4-4-12)",
                "session_id": sid}
    if not turns:
        return {"ok": False, "error_code": "NO_TURNS",
                "error": "turns is empty; a conversation with no turn is not a "
                         "conversation"}
    st = (status or "").strip()
    if st not in WORKFLOW_STATUSES:
        return {"ok": False, "error_code": "INVALID_STATUS",
                "error": "status %r is not one of %s"
                         % (status, ", ".join(WORKFLOW_STATUSES))}

    # THE IDENTITY FIRST, through the EXISTING entry point, so the chat_id is
    # the same INTEGER every other writer would resolve.
    ident = register_chat_identity(sid, source=source, ide=ide, llm=llm)
    if not ident.get("ok"):
        return {"ok": False, "error_code": "IDENTITY_FAILED",
                "error": ident.get("error") or "register_chat_identity failed",
                "identity": ident}
    chat_id = ident.get("chat_id")
    sha256 = ident.get("sha256") or ""
    chat_hash = ident.get("chat_hash") or ""

    written: list[dict[str, Any]] = []
    for i, turn in enumerate(turns):
        role = str(turn.get("role") or "Question").strip()
        if role not in ("Question", "Answer"):
            return {"ok": False, "error_code": "BAD_ROLE",
                    "error": "turn %d role %r is not Question or Answer"
                             % (i, role),
                    "written": written}
        content = str(turn.get("content") or "")
        if not content.strip():
            return {"ok": False, "error_code": "EMPTY_TURN",
                    "error": "turn %d has no content" % i,
                    "written": written}
        res = create_chat_center_message(
            session_id=sid, chat_id=chat_id, sha256=sha256,
            chat_hash=chat_hash, role=role, content=content,
            title=str(turn.get("title") or "") or None,
            status=st, ide=ide, llm=llm,
        )
        if not res.get("ok"):
            return {"ok": False, "error_code": "WRITE_FAILED",
                    "error": res.get("error") or "create failed",
                    "turn": i, "written": written}
        written.append({"id": res.get("id"), "role": role})
    return {"ok": True, "session_id": sid, "chat_id": chat_id,
            "status": st, "written": written, "count": len(written)}


def set_chat_message_status(
    message_id: int,
    status: str,
    *,
    measured_effect: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Move ONE `chat_center_message.status`, optionally recording a note.

    THE MISSING UPDATE PATH, and it is a MEASURED hole: a repo-wide search for
    `UPDATE chat_center_message` returned **NOTHING**, so `status='ask'` could be
    WRITTEN and never MOVED — the amber list (`chat-center-list.js`: "a `ask` is
    amber on purpose: it is the one state that needs a HUMAN") only grew.

    IT LIVES HERE, NEXT TO THE INSERT, on purpose: this is the ONE module that
    knows the message table, so the status vocabulary is checked in ONE place.
    A second module writing this table would be a second place that knows the
    rule, and the two would drift.

    `measured_effect` is the NOTE slot the table already uses for a citation-grade
    evidence string (measured: every live use is a reference or a reading, never
    prose) — so an acknowledgment's WHO/WHY fits the existing column rather than
    needing a new one.

    `conn` LETS A CALLER WITH AN OPEN TRANSACTION REUSE IT. MEASURED, and this bit
    me twice: opening a SECOND connection to the same file while the caller holds
    an uncommitted write is a **self-deadlock** (`database is locked`, and no
    timeout helps because the caller's transaction never ends while it waits). So
    a caller that already has a transaction passes it in, and this function does
    NOT close a connection it did not open. A `conn` of `None` keeps the
    standalone behaviour (open, use, close).
    """
    st = str(status or "").strip()
    if not st:
        return {"ok": False, "error_code": "MISSING_STATUS",
                "error": "status is required"}
    if st not in WORKFLOW_STATUSES:
        return {
            "ok": False,
            "error_code": "INVALID_STATUS",
            "error": ("status %r is not one of %s; an unknown workflow state "
                      "cannot be filtered or counted"
                      % (status, ", ".join(WORKFLOW_STATUSES))),
        }
    # A CALLER-SUPPLIED CONNECTION IS REUSED, NOT REOPENED (see the docstring:
    # a second connection during the caller's open transaction is a self-deadlock).
    own = conn is None
    use = _conn() if own else conn
    try:
        row = use.execute("SELECT status FROM chat_center_message WHERE id=?",
                          (int(message_id),)).fetchone()
        if not row:
            return {"ok": False, "error_code": "NOT_FOUND",
                    "error": "no chat_center_message with id=%d" % int(message_id)}
        use.execute(
            "UPDATE chat_center_message SET status=?, measured_effect=? "
            "WHERE id=?",
            (st, measured_effect, int(message_id)))
        use.commit()
        out = use.execute("SELECT * FROM chat_center_message WHERE id=?",
                          (int(message_id),)).fetchone()
        return {"ok": True, "id": int(message_id), "from": row["status"],
                "to": st, "row": dict(out) if out else None}
    finally:
        # ONLY a connection WE OPENED is closed; the caller's stays theirs.
        if own:
            use.close()
    lim = max(1, min(int(limit or 50), 500))
    clauses: list[str] = []
    args: list[Any] = []
    if chat_id is not None and str(chat_id).strip() != "":
        raw = str(chat_id).strip()
        try:
            clauses.append("chat_id = ?")
            args.append(int(raw))
        except ValueError:
            # legacy sha256 text
            clauses.append("sha256 = ?")
            args.append(raw.lower())
    if session_id:
        clauses.append("session_id = ?")
        args.append(str(session_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT id, session_id, chat_id, sha256, role, content,
                       title, event, evidence_ref, rule_version, measured_effect,
                       status,
                       catalog_id, catalog_name, subcatalog_id, subcatalog_name,
                       skill_id, skill_name, llm, ide, answered_at, created_at
                FROM chat_center_message
                {where}
                ORDER BY id ASC
                LIMIT ?
                """,
                (*args, lim),
            ).fetchall()
        ]
        total = conn.execute(
            f"SELECT COUNT(*) FROM chat_center_message{where}", args
        ).fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "rows": rows, "count": len(rows), "total": int(total)}


def _sort_key(entity: dict[str, Any]) -> tuple[int, int]:
    """Ontology sort rank; optional types (Job/Event) sort after fixed order."""
    typ = str(entity.get("type") or "")
    if typ in ONTOLOGY_ORDER:
        return (ONTOLOGY_ORDER.index(typ), 0)
    if typ in OPTIONAL_TYPES:
        return (len(ONTOLOGY_ORDER) + OPTIONAL_SORT.get(typ, 99), 0)
    return (len(ONTOLOGY_ORDER) + 100, 0)

# ---- RBAC helpers ----


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def get_user_by_token(token: str) -> dict[str, Any] | None:
    """Return active user + role + permissions for a Bearer token, or None."""
    if not token:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT u.user_id, u.name, u.api_token, u.status,
                   r.role_id, r.role_name, r.permissions
            FROM users u
            JOIN roles r ON r.role_id = u.role_id
            WHERE u.api_token = ? AND u.status = 'active'
            """,
            (token,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["permissions"] = json.loads(d.get("permissions") or "[]")
    except (TypeError, json.JSONDecodeError):
        d["permissions"] = []
    return d


def require_permission(token: str, perm: str) -> dict[str, Any] | None:
    """Return user dict if token valid + has perm, else None."""
    user = get_user_by_token(token)
    if not user:
        return None
    if perm not in user.get("permissions", []):
        return None
    return user


def auth_error(msg: str = "unauthorized") -> tuple[dict[str, Any], int]:
    return ({"ok": False, "error": msg}, 401)


def forbidden_error(msg: str = "forbidden: missing permission") -> tuple[dict[str, Any], int]:
    return ({"ok": False, "error": msg}, 403)


def _bearer_token(headers: Any) -> str:
    auth = (headers.get("Authorization") or "") if hasattr(headers, "get") else ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


# ---- Skill Library data access ----


def list_skills() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT s.skill_id, s.description, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM skill_versions v
                     WHERE v.skill_id = s.skill_id) AS version_count
            FROM skills s ORDER BY s.skill_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_skill(skill_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT skill_id, description, created_at, updated_at FROM skills WHERE skill_id=?",
            (skill_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_skill_versions(skill_id: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT sv_id, skill_id, version, status, created_at, updated_at
            FROM skill_versions WHERE skill_id=? ORDER BY version
            """,
            (skill_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_skill_version(skill_id: str, version: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT sv_id, skill_id, version, status, bundle_yaml, bundle_schema,
                   prompt_ask, prompt_confirm, prompt_plan, created_at, updated_at
            FROM skill_versions WHERE skill_id=? AND version=?
            """,
            (skill_id, version),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_latest_published(skill_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT sv_id, skill_id, version, status, bundle_yaml, bundle_schema,
                   prompt_ask, prompt_confirm, prompt_plan, created_at, updated_at
            FROM skill_versions
            WHERE skill_id=? AND status='published'
            ORDER BY version DESC LIMIT 1
            """,
            (skill_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def create_skill(skill_id: str, description: str) -> dict[str, Any]:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO skills (skill_id, description) VALUES (?, ?)",
            (skill_id, description),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "skill_id": skill_id}


def create_skill_version(
    skill_id: str,
    version: str,
    *,
    bundle_yaml: str = "",
    bundle_schema: str = "",
    prompt_ask: str = "",
    prompt_confirm: str = "",
    prompt_plan: str = "",
) -> dict[str, Any]:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO skills (skill_id) VALUES (?)",
            (skill_id,),
        )
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO skill_versions
                (skill_id, version, status, bundle_yaml, bundle_schema,
                 prompt_ask, prompt_confirm, prompt_plan)
            VALUES (?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (skill_id, version, bundle_yaml, bundle_schema,
             prompt_ask, prompt_confirm, prompt_plan),
        )
        conn.commit()
        return {"ok": True, "sv_id": int(cur.lastrowid) if cur.lastrowid else 0}
    finally:
        conn.close()


def set_version_status(skill_id: str, version: str, status: str) -> dict[str, Any]:
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE skill_versions SET status=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE skill_id=? AND version=?",
            (status, skill_id, version),
        )
        conn.commit()
        return {"ok": True, "changed": int(cur.rowcount)}
    finally:
        conn.close()


# ---- Skill Library sync (register scanned skills) + catalog tree ----

# Folder name -> catalog label (the skills/ top-level folders are the catalogs).
SKILL_CATALOG_FOLDERS = {
    "1_core": ("core", "Core skills: membership, task upsert, scanner, lifecycle"),
    "2_db_schema": ("db_schema", "Database schema & field registration skills"),
    "3_ui": ("ui", "UI / frontend builder skills"),
    "4_agent": ("agent", "Agent workflow skills: research, proposal, design"),
    "5_qa": ("qa", "QA / validation skills"),
}


# The catalog columns added to `skills` by additive migration. DECLARED HERE as a
# CONSTANT so a reader can see the table's full declared shape; the migration
# below reads this TUPLE rather than repeating the pairs, so there is ONE source
# and the two can never disagree.
_SKILL_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("catalog_id", "INTEGER DEFAULT 0"),
    ("subcatalog_id", "INTEGER DEFAULT 0"),
    ("catalog_name", "TEXT DEFAULT ''"),
    ("subcatalog_name", "TEXT DEFAULT ''"),
)


def _ensure_skill_catalog_columns() -> None:
    """Add catalog_id / subcatalog_id / catalog_name / subcatalog_name to skills."""
    conn = _conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(skills)").fetchall()}
        for col, ddl in _SKILL_CATALOG_COLUMNS:
            if col not in cols:
                conn.execute(f"ALTER TABLE skills ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        conn.close()


def sync_skill_library() -> dict[str, Any]:
    """Scan skills/ via skill_scanner and upsert each skill + a v1.0.0 version.

    Idempotent: existing skills/versions are left untouched (INSERT OR IGNORE).
    Returns {ok, added, updated, total}.
    """
    _ensure_skill_catalog_columns()
    try:
        from skill_scanner import scan_skill_folder
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    skills_dir = BASE_DIR / "skills"
    if not skills_dir.is_dir():
        return {"ok": False, "error": f"skills dir not found: {skills_dir}"}

    scanned = scan_skill_folder(skills_dir)
    added = 0
    updated = 0
    for task in scanned:
        skill_id = str(task.get("name") or "").strip()
        if not skill_id:
            continue
        description = str(task.get("qc_summary") or task.get("reason") or "").strip()
        prompt = str(task.get("prompt") or "").strip()
        catalog_id = int(task.get("catalog_id") or 0)
        subcatalog_id = int(task.get("subcatalog_id") or 0)
        catalog_name = str(task.get("catalog_name") or "").strip()
        subcatalog_name = str(task.get("subcatalog_name") or "").strip()

        # The skills/ folder structure is the authoritative catalog. Derive the
        # catalog from the skill's folder and OVERRIDE any frontmatter catalog_id
        # (which may point at an unrelated sample catalog like membership).
        rel = str(task.get("skill_path") or "")
        folder_cat = ""
        for folder, (label, _desc) in SKILL_CATALOG_FOLDERS.items():
            if f"{folder}{chr(92)}" in rel or f"/{folder}/" in rel:
                folder_cat = label
                break
        if folder_cat:
            catalog_name = folder_cat
            catalog_id = 0
            subcatalog_id = 0
            subcatalog_name = ""

        # Upsert skill row (INSERT OR IGNORE keeps existing description).
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT description FROM skills WHERE skill_id=?", (skill_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE skills SET catalog_id=?, subcatalog_id=?, "
                    "catalog_name=?, subcatalog_name=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE skill_id=?",
                    (catalog_id, subcatalog_id, catalog_name, subcatalog_name, skill_id),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO skills (skill_id, description, catalog_id, "
                    "subcatalog_id, catalog_name, subcatalog_name) VALUES (?,?,?,?,?,?)",
                    (skill_id, description, catalog_id, subcatalog_id,
                     catalog_name, subcatalog_name),
                )
                added += 1
            conn.commit()
        finally:
            conn.close()

        # Upsert v1.0.0 version (INSERT OR IGNORE keeps existing).
        if prompt:
            create_skill_version(
                skill_id,
                "v1.0.0",
                bundle_yaml=prompt,
                bundle_schema=json.dumps(
                    {"task_id": task.get("task_id"), "schema": task.get("schema")},
                    ensure_ascii=False,
                ),
            )

    return {"ok": True, "added": added, "updated": updated, "total": len(scanned)}


def list_skill_catalogs() -> list[dict[str, Any]]:
    """Return catalog > subcatalog > skills tree from the skills table."""
    _ensure_skill_catalog_columns()
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT s.skill_id, s.description, s.catalog_id, s.subcatalog_id,
                   s.catalog_name, s.subcatalog_name,
                   (SELECT COUNT(*) FROM skill_versions v
                     WHERE v.skill_id = s.skill_id) AS version_count,
                   (SELECT status FROM skill_versions v
                     WHERE v.skill_id = s.skill_id
                     ORDER BY v.version DESC LIMIT 1) AS latest_status
            FROM skills s ORDER BY s.skill_id
            """
        ).fetchall()
    finally:
        conn.close()

    catalogs: dict[str, dict[str, Any]] = {}
    for r in rows:
        cat_name = r["catalog_name"] or "uncategorized"
        sub_name = r["subcatalog_name"] or "general"
        cat = catalogs.setdefault(
            cat_name,
            {"name": cat_name, "subcatalogs": {}},
        )
        sub = cat["subcatalogs"].setdefault(
            sub_name,
            {"name": sub_name, "skills": []},
        )
        sub["skills"].append(
            {
                "skill_id": r["skill_id"],
                "description": r["description"],
                "version_count": r["version_count"],
                "latest_status": r["latest_status"],
            }
        )

    # Sort catalogs by folder order, subcatalogs by name, skills by id.
    order = {label: i for i, (label, _d) in enumerate(SKILL_CATALOG_FOLDERS.values())}
    result = []
    for name, cat in catalogs.items():
        subs = []
        for sub_name, sub in cat["subcatalogs"].items():
            sub["skills"].sort(key=lambda s: s["skill_id"])
            subs.append(sub)
        subs.sort(key=lambda s: s["name"])
        result.append(
            {
                "name": name,
                "order": order.get(name, 99),
                "subcatalogs": subs,
                "skill_count": sum(len(s["skills"]) for s in subs),
            }
        )
    result.sort(key=lambda c: (c["order"], c["name"]))
    return result


# ---- llm_tasks DB write ----


def insert_llm_task(
    *,
    task_id: str,
    root_seq: str,
    type_: str,
    name: str,
    action: str,
    session_id: str | None,
    chat_id: str | None,
    created_by: int | None,
) -> dict[str, Any]:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_tasks
                (task_id, root_seq, type, name, action, session_id, chat_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, root_seq, type_, name, action, session_id, chat_id, created_by),
        )
        conn.commit()
        return {"ok": True, "task_id": task_id}
    finally:
        conn.close()


# ---- Interactive plan sessions (Ask -> Confirm -> Plan) ----

PLAN_SESSION_TTL_SECONDS = 24 * 3600  # 24h expiry


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def session_error(
    code: str,
    message: str,
    *,
    http_status: int = 400,
    session_id: str | None = None,
    track_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Standardized error JSON structure for plan-session endpoints."""
    return (
        {
            "ok": False,
            "error_code": code,
            "error": message,
            "session_id": session_id,
            "track_id": track_id,
        },
        http_status,
    )


def upsert_plan_session(
    session_id: str,
    *,
    chat_id: str | None = None,
    root_seq: str = "",
    requirement: str = "",
    stage: str = "ask",
    extracted_entities: list[dict[str, Any]] | None = None,
    expected_version: int | None = None,
    from_stage: str | None = None,
) -> dict[str, Any]:
    """Optimistic-lock upsert with state-machine transition validation.

    Session identity is the (session_id, chat_id) pair. chat_id is required.
    Returns {"ok": True, ...} or {"ok": False, "error_code": ...}.
    """
    if not chat_id:
        return {
            "ok": False,
            "error_code": "MISSING_CHAT_ID",
            "error": "chat_id is required for plan sessions",
            "session_id": session_id,
        }
    ensure_chat_id(chat_id)
    lock = _session_lock(session_id, chat_id)
    with lock:
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT * FROM plan_sessions WHERE session_id=? AND chat_id=?",
                (session_id, chat_id),
            ).fetchone()
            cur_stage = dict(existing)["stage"] if existing else "new"
            cur_version = int(dict(existing)["version"]) if existing else 0

            # State machine: validate transition
            allowed = STAGE_TRANSITIONS.get(cur_stage, set())
            if stage not in allowed:
                return {
                    "ok": False,
                    "error_code": "INVALID_STAGE_TRANSITION",
                    "error": (
                        f"cannot move {cur_stage} -> {stage}; "
                        f"allowed: {sorted(allowed)}"
                    ),
                    "session_id": session_id,
                    "chat_id": chat_id,
                }

            # Optimistic lock: caller must pass the version it read
            if expected_version is not None and expected_version != cur_version:
                return {
                    "ok": False,
                    "error_code": "VERSION_CONFLICT",
                    "error": (
                        f"version conflict: expected {expected_version}, "
                        f"current {cur_version}"
                    ),
                    "session_id": session_id,
                    "chat_id": chat_id,
                }

            chat_hash = chat_pair_hash(chat_id, session_id)
            new_version = cur_version + 1
            conn.execute(
                """
                INSERT INTO plan_sessions
                    (session_id, chat_id, chat_hash, root_seq, requirement, stage,
                     extracted_entities, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, chat_id) DO UPDATE SET
                    chat_hash = excluded.chat_hash,
                    root_seq = excluded.root_seq,
                    requirement = excluded.requirement,
                    stage = excluded.stage,
                    extracted_entities = excluded.extracted_entities,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id, chat_id, chat_hash, root_seq, requirement, stage,
                    json.dumps(extracted_entities or [], ensure_ascii=False),
                    new_version, _now_iso(),
                ),
            )
            conn.commit()
            return {
                "ok": True,
                "session_id": session_id,
                "chat_id": chat_id,
                "stage": stage,
                "version": new_version,
                "chat_hash": chat_hash,
            }
        finally:
            conn.close()


def log_plan_session(
    *,
    session_id: str,
    chat_id: str | None = None,
    stage: str = "",
    from_stage: str | None = None,
    action: str = "",
    payload: Any = None,
    state: Any = None,
    track_id: str | None = None,
    qc_warnings: list[str] | None = None,
    qc_errors: list[str] | None = None,
) -> None:
    """Append-only audit log for plan-session steps (never a gate)."""
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO plan_session_log
                (session_id, chat_id, chat_hash, stage, from_stage, action,
                 payload, state, track_id, qc_warnings, qc_errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, chat_id,
                chat_pair_hash(chat_id or "", session_id),
                stage, from_stage, action,
                json.dumps(payload, ensure_ascii=False, default=str),
                json.dumps(state, ensure_ascii=False, default=str),
                track_id,
                json.dumps(qc_warnings or [], ensure_ascii=False),
                json.dumps(qc_errors or [], ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_plan_session(
    session_id: str, chat_id: str | None = None
) -> dict[str, Any] | None:
    """Return a plan session by (session_id, chat_id) pair.

    If chat_id is omitted, returns the row only when the session_id is unique
    (legacy single-row sessions); otherwise None (pair required).
    """
    conn = _conn()
    try:
        if chat_id:
            row = conn.execute(
                "SELECT * FROM plan_sessions WHERE session_id=? AND chat_id=?",
                (session_id, chat_id),
            ).fetchone()
        else:
            rows = conn.execute(
                "SELECT * FROM plan_sessions WHERE session_id=?", (session_id,)
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["extracted_entities"] = json.loads(d.get("extracted_entities") or "[]")
    except (TypeError, json.JSONDecodeError):
        d["extracted_entities"] = []
    return d


def delete_plan_session(session_id: str, chat_id: str | None = None) -> int:
    """Delete a plan session by (session_id, chat_id) pair.

    If chat_id is omitted, deletes only when the session_id is unique.
    """
    conn = _conn()
    try:
        if chat_id:
            cur = conn.execute(
                "DELETE FROM plan_sessions WHERE session_id=? AND chat_id=?",
                (session_id, chat_id),
            )
        else:
            cur = conn.execute(
                "DELETE FROM plan_sessions WHERE session_id=? "
                "AND (SELECT COUNT(*) FROM plan_sessions p2 "
                "     WHERE p2.session_id = plan_sessions.session_id) = 1",
                (session_id,),
            )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def cleanup_expired_plan_sessions(
    ttl_seconds: int = PLAN_SESSION_TTL_SECONDS,
) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM plan_sessions WHERE updated_at < datetime('now', ?)",
            (f"-{ttl_seconds} seconds",),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def apply_user_modify(
    entities: list[dict[str, Any]],
    user_modify: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply {'remove':[...], 'add':[...]} to entity list.

    Returns (new_list, errors). Errors include duplicate-name additions
    (hard-intercepted at Confirm stage, not deferred to Generate).
    """
    out = list(entities)
    errors: list[str] = []
    if not isinstance(user_modify, dict):
        return out, errors
    for rm in user_modify.get("remove") or []:
        if isinstance(rm, dict):
            out = [
                e
                for e in out
                if not (
                    e.get("type") == rm.get("type")
                    and e.get("name") == rm.get("name")
                )
            ]
    for add in user_modify.get("add") or []:
        if isinstance(add, dict) and add.get("type") and add.get("name"):
            dup = any(
                e.get("type") == add["type"] and e.get("name") == add["name"]
                for e in out
            )
            if dup:
                errors.append(
                    f"duplicate entity: {add['type']} '{add['name']}' already exists"
                )
                continue
            out.append(
                {
                    "type": add["type"],
                    "name": add["name"],
                    "action": add.get("action", "CREATE"),
                }
            )
    return out, errors


# ---- Plan validation layer (hard rules, not LLM) ----


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a plan dict against hard rules.

    Rules:
    1. Task IDs continuous + unique (root_seq.N, N starts at 1, no gaps/dupes).
    2. Each Field is its own task (never merged).
    3. Entity sort follows ontology hierarchy order.
    4. JSON structure conforms to schema.
    Returns {"ok": True} or {"ok": False, "errors": [...]}.
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["plan must be an object"]}

    root_seq = str(plan.get("root_seq") or "").strip()
    if not root_seq:
        errors.append("root_seq is required")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks must be a non-empty list")
        return {"ok": False, "errors": errors}

    # Rule 1: continuous + unique task IDs
    seen: set[str] = set()
    expected_seq = 1
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            errors.append(f"tasks[{i}] must be an object")
            continue
        tid = str(t.get("task_id") or "").strip()
        if not tid:
            errors.append(f"tasks[{i}].task_id is required")
            continue
        if tid in seen:
            errors.append(f"duplicate task_id: {tid}")
        seen.add(tid)
        # continuous sequence check
        m = re.match(rf"^{re.escape(root_seq)}\.(\d+)$", tid)
        if not m:
            errors.append(f"task_id {tid} does not match root_seq.N pattern")
        else:
            seq = int(m.group(1))
            if seq != expected_seq:
                errors.append(
                    f"task_id {tid} breaks continuity: expected {root_seq}.{expected_seq}"
                )
            expected_seq += 1

    # Rule 2: each Field is its own task (name uniqueness among Field tasks)
    field_names: set[str] = set()
    for t in tasks:
        if str(t.get("type") or "") == "Field":
            nm = str(t.get("name") or "").strip()
            if not nm:
                errors.append("Field task missing name")
            elif nm in field_names:
                errors.append(f"Field merged/duplicated: {nm} (one task per field)")
            field_names.add(nm)

    # Rule 3: ontology sort order (non-decreasing rank)
    rank = {t: i for i, t in enumerate(ONTOLOGY_ORDER)}
    rank.update({t: len(ONTOLOGY_ORDER) + i for i, t in enumerate(OPTIONAL_TYPES)})
    last_rank = -1
    for t in tasks:
        typ = str(t.get("type") or "")
        if typ not in rank:
            errors.append(f"unknown entity type: {typ}")
            continue
        r = rank[typ]
        if r < last_rank:
            errors.append(
                f"sort order violation: {typ} appears after a lower-rank type"
            )
        last_rank = r

    # Rule 4: action validity
    for t in tasks:
        act = str(t.get("action") or "")
        if act not in VALID_ACTIONS:
            errors.append(f"invalid action: {act!r} (must be CREATE/UPDATE/DELETE)")

    # optional_extensions (if present) must be a list
    opt = plan.get("optional_extensions")
    if opt is not None and not isinstance(opt, list):
        errors.append("optional_extensions must be a list")

    return {"ok": not errors, "errors": errors}


# ---- Worker Plan Mode flow ----


def run_plan_flow(requirement: str, root_seq: str) -> dict[str, Any]:
    """Ask -> Confirm -> Plan orchestration.

    In Plan Mode this produces a plan only — it NEVER writes to llm_tasks.
    (LLM calls are stubbed here; the worker engine wires the real local Qwen.)
    """
    # Stage 1: Ask — extract entities (LLM in real flow; here a deterministic parser)
    entities = _extract_entities(requirement)
    # Stage 2: Confirm — count tasks, list optional extensions
    total = len(entities)
    optional = [
        {"type": "Job", "name": "seed_data", "action": "CREATE"},
        {"type": "Event", "name": "data_changed", "action": "CREATE"},
    ]
    # Stage 3: Plan — sort by ontology, assign continuous IDs
    entities.sort(key=_sort_key)
    tasks = []
    for i, e in enumerate(entities, start=1):
        tasks.append(
            {
                "task_id": f"{root_seq}.{i}",
                "type": e["type"],
                "name": e["name"],
                "action": e.get("action", "CREATE"),
            }
        )
    plan = {
        "root_seq": root_seq,
        "total_tasks": total,
        "tasks": tasks,
        "optional_extensions": optional,
    }
    validation = validate_plan(plan)
    return {
        "stage": "plan",
        "requirement": requirement,
        "entities": entities,
        "total_tasks": total,
        "plan": plan,
        "validation": validation,
        "note": "Plan Mode: no API writes performed",
    }


def _extract_entities(requirement: str) -> list[dict[str, Any]]:
    """Deterministic entity extraction for the phone+region test-system example.

    In the real worker this is replaced by the local LLM Ask stage. This parser
    recognizes the documented example so the flow is testable end-to-end.
    """
    req = requirement.lower()
    entities: list[dict[str, Any]] = []
    if "phone" in req and "region" in req and ("test system" in req or "test1" in req):
        entities = [
            {"type": "Channel", "name": "local", "action": "CREATE"},
            {"type": "Module", "name": "test", "action": "CREATE"},
            {"type": "Capability", "name": "member data", "action": "CREATE"},
            {"type": "API", "name": "member_data_api", "action": "CREATE"},
            {"type": "Function", "name": "get_member", "action": "CREATE"},
            {"type": "Table", "name": "test1", "action": "CREATE"},
            {"type": "Field", "name": "phone", "action": "CREATE"},
            {"type": "Field", "name": "region", "action": "CREATE"},
        ]
    return entities


# ---- Worker Plan Mode system prompt (ontology_task_planner bundle) ----

PLAN_MODE_SYSTEM_PROMPT = """# SKILL: ontology_task_planner
## Ontology Hierarchy (fixed order)
Channel -> Module -> Capability -> API -> Function -> Table -> Field
Optional extra entity types: Job, Event

## Task Rules
1. Each entity = one separate task.
2. Field: ONE task PER FIELD, never combine multiple fields into one task.
3. Task ID coding rule: root_seq.N, continuous sequence, DO NOT reset counter when entity type changes.
4. Action can be CREATE / UPDATE / DELETE.
5. Plan Mode: ONLY produce task plan. DO NOT execute API calls, do not write data.
6. Execute Mode only starts after user explicitly confirms the plan.

## 3 Stage workflow: Ask -> Confirm -> Plan
### Stage 1: Ask
Extract requirement from user input:
- entity types
- entity names
- action
Output: preliminary identified entity list.

### Stage 2: Confirm
Calculate total tasks count.
List optional extra entities (Job / Event).
Ask user to confirm scope.
DO NOT generate final task ID list at this stage.
If user modify scope, re-calculate count and re-confirm.

### Stage 3: Plan
After user confirmed scope:
1. Sort entities strictly follow ontology hierarchy order.
2. Assign continuous task ID: root_seq.1, root_seq.2 ...
3. Output 2 parts: markdown table + structured JSON.
4. Include optional_extensions block for Job/Event if applicable.

## Output JSON Schema
{
  "root_seq": "string",
  "total_tasks": integer,
  "tasks": [
    {"task_id":"string","type":"string","name":"string","action":"string"}
  ],
  "optional_extensions": [
    {"type":"string","name":"string","action":"string","new_task_id":"string"}
  ]
}

## Constraint
- Never skip any ontology level if entity is required.
- No duplicate task_id.
- Do not merge multiple fields into one task.
"""

PROMPT_ASK = """You are in Ask stage.
Read user requirement, extract all entities, their type, name and action.
Only output preliminary entity list, NO task ID, NO counting yet.
Do not add optional Job/Event in this step.
"""

PROMPT_CONFIRM = """You are in Confirm stage.
Take extracted entity list from Ask stage.
Count total tasks according to ontology rule: each entity is one task, each field separate task.
List all entities, total tasks count.
List optional possible extensions: Job (seed data), Event.
Ask user: confirm scope? or add/remove entity?
DO NOT generate task IDs.
"""

PROMPT_PLAN = """You are in Plan stage, user has confirmed scope.
Follow ontology hierarchy sort order.
Assign continuous task_id root_seq.1, root_seq.2...
Generate markdown table + JSON following schema.
Add optional_extensions block for Job / Event if relevant.
Plan Mode: NO API write operation.
"""


def seed_ontology_task_planner() -> dict[str, Any]:
    """Seed the ontology_task_planner skill bundle (v1.0.0) into skill_versions."""
    skill_id = "ontology_task_planner"
    version = "v1.0.0"
    create_skill(skill_id, "Ontology task planner: Ask -> Confirm -> Plan mode")
    return create_skill_version(
        skill_id,
        version,
        bundle_yaml=PLAN_MODE_SYSTEM_PROMPT,
        bundle_schema=json.dumps(
            {
                "root_seq": "string",
                "total_tasks": "integer",
                "tasks": [
                    {
                        "task_id": "string",
                        "type": "string",
                        "name": "string",
                        "action": "string",
                    }
                ],
                "optional_extensions": [
                    {
                        "type": "string",
                        "name": "string",
                        "action": "string",
                        "new_task_id": "string",
                    }
                ],
            },
            indent=2,
        ),
        prompt_ask=PROMPT_ASK,
        prompt_confirm=PROMPT_CONFIRM,
        prompt_plan=PROMPT_PLAN,
    )


# ---- TTL background cleanup (prevents plan_sessions table growth) ----


def _ttl_cleanup_loop(interval_seconds: int = 3600) -> None:
    while True:
        try:
            n = cleanup_expired_plan_sessions()
            # Heartbeat: record each cleanup run so a crashed thread is detectable.
            try:
                log_plan_session(
                    session_id="__ttl__",
                    stage="ttl",
                    action="ttl_cleanup",
                    payload={"cleaned": n},
                    state={"interval_seconds": interval_seconds},
                )
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(interval_seconds)


def start_ttl_cleanup(interval_seconds: int = 3600) -> threading.Thread:
    """Start a daemon thread that periodically purges expired plan_sessions."""
    t = threading.Thread(
        target=_ttl_cleanup_loop, args=(interval_seconds,), daemon=True
    )
    t.start()
    return t


# ---- TTL heartbeat monitoring (independent of the cleanup thread) ----


def get_latest_ttl_heartbeat() -> dict[str, Any] | None:
    """Latest ttl_cleanup heartbeat row, or None."""
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT log_id, created_at, action, payload
            FROM plan_session_log
            WHERE session_id=? AND action=?
            ORDER BY log_id DESC LIMIT 1
            """,
            (HEARTBEAT_SESSION_ID, HEARTBEAT_ACTION),
        ).fetchone()
    except sqlite3.OperationalError:
        # Table missing (fresh DB) -> treat as no heartbeat.
        return None
    finally:
        conn.close()
    return dict(row) if row else None


def _iso_to_ts(iso: str) -> int:
    """Parse '%Y-%m-%d %H:%M:%S' (UTC) to unix timestamp."""
    from datetime import datetime, timezone

    try:
        dt = datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def check_ttl_heartbeat_alarm() -> tuple[bool, str]:
    """Check TTL heartbeat; returns (is_firing, message). Debounced on state change."""
    global _last_heartbeat_alert_firing
    import logging

    logger = logging.getLogger(__name__)
    now_ts = int(time.time())
    hb = get_latest_ttl_heartbeat()

    if hb is None:
        msg = (
            "TTL HEARTBEAT ALERT: no ttl_cleanup heartbeat found; "
            "cleanup thread never started or crashed"
        )
        current_firing = True
    else:
        hb_ts = _iso_to_ts(str(hb.get("created_at") or ""))
        age = now_ts - hb_ts
        if age > HEARTBEAT_MAX_AGE:
            msg = (
                f"TTL HEARTBEAT ALERT: heartbeat {age}s old, "
                f"exceeds threshold {HEARTBEAT_MAX_AGE}s; cleanup thread stalled"
            )
            current_firing = True
        else:
            msg = f"TTL heartbeat OK, last heartbeat {age}s ago"
            current_firing = False

    # Debounce: alert only on state transition
    if current_firing != _last_heartbeat_alert_firing:
        if current_firing:
            logger.error(msg)
            call_alert_webhook(msg)  # send to configured channel
        else:
            logger.info("TTL HEARTBEAT RECOVERY: cleanup thread heartbeat restored")
        _last_heartbeat_alert_firing = current_firing
    return current_firing, msg


def _heartbeat_monitor_loop() -> None:
    """Independent daemon thread: polls heartbeat, separate from TTL cleanup."""
    import logging

    logger = logging.getLogger(__name__)
    while True:
        if HEARTBEAT_ALERT_ENABLE:
            try:
                check_ttl_heartbeat_alarm()
            except Exception:
                logger.exception("heartbeat monitor error")
        time.sleep(HEARTBEAT_CHECK_INTERVAL)


def start_heartbeat_monitor() -> threading.Thread:
    """Start the independent heartbeat monitor thread."""
    t = threading.Thread(
        target=_heartbeat_monitor_loop, daemon=True, name="ttl-heartbeat-monitor"
    )
    t.start()
    return t


# ---- Multi-channel webhook notifications (Telegram / WhatsApp / WeChat) ----

# ---- Alert history (persisted, viewable in the ops dashboard) ----
ALERT_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS alert_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    level       TEXT,
    message     TEXT,
    fired_at    TIMESTAMP,
    channel     TEXT,
    success     INTEGER,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_history_fired
    ON alert_history (fired_at DESC);
"""


def ensure_alert_history_table() -> None:
    """Create the alert_history table if missing (idempotent)."""
    conn = _conn()
    try:
        conn.executescript(ALERT_HISTORY_DDL)
        conn.commit()
    finally:
        conn.close()


def record_alert_event(
    *,
    source: str,
    level: str,
    message: str,
    channel: str | None = None,
    success: bool = False,
    payload: Any = None,
) -> int:
    """Insert one alert-history row. Returns row id."""
    ensure_alert_history_table()
    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO alert_history
                (source, level, message, fired_at, channel, success, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                level,
                message,
                _now_iso(),
                channel,
                1 if success else 0,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_alert_history(limit: int = 100) -> list[dict[str, Any]]:
    """Return latest alert-history rows (newest first)."""
    ensure_alert_history_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM alert_history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "null")
        except (TypeError, json.JSONDecodeError):
            d["payload"] = None
        out.append(d)
    return out


# ---- Operational maintenance (prune + slow-SQL metrics + background task) ----
# Config (tunable; defaults chosen for long soak runs)
ALERT_HISTORY_RETENTION_DAYS = 30      # keep 30 days of alert history
RATE_LIMIT_WINDOW_SEC = 60             # rate_limit rows only within window
PRUNE_INTERVAL_SECONDS = 600           # background prune every 10 min
PRUNE_BATCH_LIMIT = 500                # max rows deleted per batch (avoid lock)
SLOW_SQL_THRESHOLD_MS = 200            # log queries slower than this

# In-memory DB health metrics (exposed via /api/watchdog/events)
_db_metrics_lock = threading.Lock()
_db_slow_query_count = 0
_db_lock_count = 0


def _db_record_slow_query() -> None:
    global _db_slow_query_count
    with _db_metrics_lock:
        _db_slow_query_count += 1


def _db_record_lock() -> None:
    global _db_lock_count
    with _db_metrics_lock:
        _db_lock_count += 1


def get_db_health_metrics() -> dict[str, Any]:
    """Return slow-query / db-lock counters (thread-safe)."""
    with _db_metrics_lock:
        return {
            "slow_query_count": _db_slow_query_count,
            "db_lock_count": _db_lock_count,
            "slow_query_threshold_ms": SLOW_SQL_THRESHOLD_MS,
        }


def _timed_execute(conn: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
    """Execute a statement with slow-query timing + lock detection."""
    start = time.time()
    try:
        cur = conn.execute(sql, params)
        elapsed_ms = (time.time() - start) * 1000
        if elapsed_ms > SLOW_SQL_THRESHOLD_MS:
            _db_record_slow_query()
            import logging
            logging.getLogger(__name__).warning(
                "slow sql %.0fms: %s", elapsed_ms, sql[:120]
            )
        return cur
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            _db_record_lock()
        raise


def record_rate_limit_hit(ip: str, policy: str = "default",
                          window_sec: int | None = None) -> int:
    """Insert one rate_limit row for `ip` under `policy`. Returns the row id.

    🔴 THIS FUNCTION WAS MISSING, AND THAT IS THE HALF-BUILT FEATURE.
    MEASURED 2026-09-29: `rate_limit` had a PRUNE (`prune_rate_limit` below) and
    a WINDOW (`RATE_LIMIT_WINDOW_SEC = 60`) but **NO INSERT and NO SELECT
    anywhere in the repo**. So `prune_rate_limit()` deleted 0 rows forever and
    the limiter could never limit — the table was empty (0 rows) and always
    would be.

    The design the two existing halves imply is: INSERT a row per request, prune
    the rows outside the window, COUNT the rows inside it. This is the INSERT.
    `rate_limit_hits_in_window()` below is the COUNT.

    🔴 `policy` IS NOW A PARAMETER (2026-09-29), because the limiter is
    TWO-LAYERED: the expensive LLM/vision routes get a strict quota and the rest
    get a loose one. MEASURED reason a shared counter is WRONG: a burst of cheap
    reads would exhaust the expensive budget, so the strict layer would protect
    nothing. OWASP API4:2023 states the requirement — "Rate limiting should be
    fine tuned based on the business needs. Some API Endpoints might require
    stricter policies."

    `window_sec` is the pruning horizon, NOT the decision window: it only says
    how far back a row is worth keeping. The DECISION window belongs to the
    caller (see `rate_limit_decision`), because a policy may declare one and a
    pruner must not be the thing that decides it.

    The table is created by `db_schema.RATE_LIMIT_DDL`, so this function does not
    create it — the same rule as `record_alert_event` calling
    `ensure_alert_history_table()` only because that table has no schema owner.
    """
    conn = _conn()
    try:
        # `policy` may be an ADDITIVE column on a DB that predates it. MEASURED:
        # `CREATE TABLE IF NOT EXISTS` does not add a column, so a database that
        # was built before this change has no `policy` and this INSERT would
        # raise. The migration in `db_schema` adds it; this guard keeps an
        # un-migrated database WRITING (under 'default') instead of failing
        # closed on a column that is not the caller's fault.
        has_policy = any(c[1] == "policy"
                         for c in conn.execute("PRAGMA table_info(rate_limit)"))
        if has_policy:
            cur = conn.execute(
                "INSERT INTO rate_limit (ip, policy, hit_ts) VALUES (?, ?, ?)",
                (str(ip), str(policy), time.time()),
            )
        else:
            cur = conn.execute(
                "INSERT INTO rate_limit (ip, hit_ts) VALUES (?, ?)",
                (str(ip), time.time()),
            )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def rate_limit_hits_in_window(ip: str, policy: str | None = None,
                              window_sec: int | None = None) -> int:
    """How many hits `ip` made inside the sliding window. The COUNT half.

    MEASURED 2026-09-29: this is the read the limiter needs and did not have.
    A caller decides by comparing this number against its own limit; this
    function does NOT decide, because the limit is the caller's policy, not the
    table's.

    `policy=None` counts EVERY policy — which is what the pre-existing callers
    want and what keeps this change additive. Passing a policy counts that layer
    alone.
    """
    win = RATE_LIMIT_WINDOW_SEC if window_sec is None else int(window_sec)
    conn = _conn()
    try:
        cutoff = time.time() - win
        has_policy = any(c[1] == "policy"
                         for c in conn.execute("PRAGMA table_info(rate_limit)"))
        if policy is None or not has_policy:
            return int(conn.execute(
                "SELECT COUNT(*) FROM rate_limit WHERE ip = ? AND hit_ts > ?",
                (str(ip), cutoff),
            ).fetchone()[0])
        return int(conn.execute(
            "SELECT COUNT(*) FROM rate_limit "
            "WHERE ip = ? AND policy = ? AND hit_ts > ?",
            (str(ip), str(policy), cutoff),
        ).fetchone()[0])
    finally:
        conn.close()


def _rate_limit_max_window() -> int:
    """The longest window any usable policy declares, or 0 when it cannot be read.

    WHY THIS IS A LOCAL HELPER AND NOT AN IMPORT AT MODULE SCOPE: MEASURED
    2026-09-29 — `rate_limit_policy.decide()` imports THIS module, so a top-level
    `import rate_limit_policy` here would be a CYCLE. It is imported inside the
    call, and it fails OPEN (returns 0) so a policy that cannot be read makes the
    pruner no more aggressive than the pre-existing constant — never more
    aggressive, because deleting rows a policy still needs would silently silence
    the stricter layer.
    """
    try:
        import rate_limit_policy as rlp
        return int(rlp.rate_limit_max_window())
    except Exception:
        return 0


def prune_rate_limit(batch_limit: int = PRUNE_BATCH_LIMIT) -> int:
    """Delete rate_limit rows outside the sliding window.

    SQLite does not support LIMIT on DELETE; the batch_limit is accepted for
    API compatibility but the delete is bounded by the window predicate.

    🔴 THE HORIZON IS THE LONGEST POLICY WINDOW (2026-09-29), not
    `RATE_LIMIT_WINDOW_SEC` alone. MEASURED reason: with two layers, a pruner
    that uses only the 60s constant would DELETE rows a policy with a longer
    window still needs — it would silently silence the stricter layer. The
    horizon is therefore read from the policies, and the constant is only the
    FLOOR it can never fall below.
    """
    horizon = max(int(RATE_LIMIT_WINDOW_SEC), int(_rate_limit_max_window()))
    conn = _conn()
    try:
        cutoff = time.time() - horizon
        cur = conn.execute(
            "DELETE FROM rate_limit WHERE hit_ts <= ?",
            (cutoff,),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def prune_alert_history(
    retention_days: int = ALERT_HISTORY_RETENTION_DAYS,
    batch_limit: int = PRUNE_BATCH_LIMIT,
) -> int:
    """Delete alert_history rows older than retention_days.

    SQLite does not support LIMIT on DELETE; bounded by the retention predicate.
    """
    ensure_alert_history_table()
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM alert_history "
            "WHERE fired_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def run_maintenance_prune() -> dict[str, Any]:
    """Run both prunes; returns counts. Never raises (best-effort)."""
    result = {"rate_limit": 0, "alert_history": 0}
    try:
        result["rate_limit"] = prune_rate_limit()
    except Exception:
        pass
    try:
        result["alert_history"] = prune_alert_history()
    except Exception:
        pass
    return result


def _maintenance_loop(interval_seconds: int = PRUNE_INTERVAL_SECONDS) -> None:
    import logging
    logger = logging.getLogger(__name__)
    while True:
        try:
            result = run_maintenance_prune()
            logger.info("maintenance prune: %s", result)
        except Exception:
            logger.exception("maintenance prune error")
        time.sleep(interval_seconds)


def start_maintenance_pruner(
    interval_seconds: int = PRUNE_INTERVAL_SECONDS,
) -> threading.Thread:
    """Start a daemon thread that periodically prunes stale rows."""
    t = threading.Thread(
        target=_maintenance_loop, args=(interval_seconds,), daemon=True
    )
    t.start()
    return t


NOTIFICATION_CHANNELS: dict[str, dict[str, str]] = {
    "telegram": {
        "label": "Telegram",
        "url_hint": "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>",
        "method": "POST",
    },
    "whatsapp": {
        "label": "WhatsApp (Cloud API)",
        "url_hint": "https://graph.facebook.com/v17.0/<PHONE_ID>/messages (Bearer token)",
        "method": "POST",
    },
    "whatsapp-personal": {
        "label": "WhatsApp (personal / gateway)",
        "url_hint": "https://api.callmebot.com/whatsapp.php?phone=<PHONE>&apikey=<APIKEY>&text=<MSG>",
        "method": "GET",
    },
    "wechat": {
        "label": "WeChat",
        "url_hint": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>",
        "method": "POST",
    },
    "generic": {
        "label": "Generic Webhook",
        "url_hint": "https://your-endpoint/alert",
        "method": "POST",
    },
}

NOTIFY_SETTING_ENABLED = "notify.enabled"          # "1"/"0"
NOTIFY_SETTING_CHANNEL = "notify.channel"          # telegram|whatsapp|wechat|generic
NOTIFY_SETTING_URL = "notify.url"                  # webhook URL
NOTIFY_SETTING_TOKEN = "notify.token"              # optional Bearer token
NOTIFY_SETTING_EXTRA = "notify.extra"              # JSON extra fields (e.g. chat_id)


def get_notify_config() -> dict[str, Any]:
    """Read notification config from the settings table (with defaults)."""
    conn = _conn()
    try:
        def _get(key: str, default: Any = None) -> Any:
            try:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (key,)
                ).fetchone()
            except sqlite3.Error:
                return default
            return row[0] if row else default

        enabled = str(_get(NOTIFY_SETTING_ENABLED, "0")) in ("1", "true", "yes", "on")
        channel = str(_get(NOTIFY_SETTING_CHANNEL, "generic"))
        url = str(_get(NOTIFY_SETTING_URL, "") or "")
        token = str(_get(NOTIFY_SETTING_TOKEN, "") or "")
        extra_raw = str(_get(NOTIFY_SETTING_EXTRA, "{}") or "{}")
        try:
            extra = json.loads(extra_raw)
        except (TypeError, json.JSONDecodeError):
            extra = {}
    finally:
        conn.close()
    return {
        "enabled": enabled,
        "channel": channel,
        "url": url,
        "token": token,
        "extra": extra,
        "channel_label": NOTIFICATION_CHANNELS.get(channel, {}).get("label", channel),
    }


def set_notify_config(
    *,
    enabled: bool | None = None,
    channel: str | None = None,
    url: str | None = None,
    token: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist notification config to the settings table."""
    conn = _conn()
    try:
        def _set(key: str, value: str) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

        if enabled is not None:
            _set(NOTIFY_SETTING_ENABLED, "1" if enabled else "0")
        if channel is not None:
            if channel not in NOTIFICATION_CHANNELS:
                raise ValueError(
                    f"unknown channel {channel!r}; "
                    f"supported: {sorted(NOTIFICATION_CHANNELS)}"
                )
            _set(NOTIFY_SETTING_CHANNEL, channel)
        if url is not None:
            _set(NOTIFY_SETTING_URL, url)
        if token is not None:
            _set(NOTIFY_SETTING_TOKEN, token)
        if extra is not None:
            _set(NOTIFY_SETTING_EXTRA, json.dumps(extra, ensure_ascii=False))
        conn.commit()
    finally:
        conn.close()
    return get_notify_config()


def call_alert_webhook(message: str) -> dict[str, Any]:
    """Send an alert to the configured channel. Returns {ok, channel, error?}."""
    cfg = get_notify_config()
    if not cfg["enabled"]:
        return {"ok": False, "channel": cfg["channel"], "skipped": "disabled"}
    if not cfg["url"]:
        return {"ok": False, "channel": cfg["channel"], "skipped": "no url configured"}
    import urllib.request

    channel = cfg["channel"]
    payload: Any
    headers = {"Content-Type": "application/json"}

    if channel == "telegram":
        payload = {"text": message}
    elif channel == "wechat":
        payload = {"msgtype": "text", "text": {"content": message}}
    elif channel == "whatsapp":
        to = cfg["extra"].get("to") or cfg["extra"].get("chat_id") or ""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }
        if cfg["token"]:
            headers["Authorization"] = "Bearer " + cfg["token"]
    elif channel == "whatsapp-personal":
        # Gateway (e.g. CallMeBot) sends to a personal WhatsApp number via GET.
        # URL template: https://api.callmebot.com/whatsapp.php?phone=<PHONE>&apikey=<APIKEY>&text=<MSG>
        phone = cfg["extra"].get("phone") or cfg["extra"].get("to") or ""
        apikey = cfg["token"] or ""
        if not phone or not apikey:
            return {"ok": False, "channel": channel,
                    "skipped": "phone (extra) and apikey (token) required"}
        import urllib.parse
        sep = "&" if "?" in cfg["url"] else "?"
        url = cfg["url"] + sep + "phone=" + urllib.parse.quote(phone) + \
            "&apikey=" + urllib.parse.quote(apikey) + \
            "&text=" + urllib.parse.quote(message)
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10):
                record_alert_event(
                    source="ttl_heartbeat", level="alert", message=message,
                    channel=channel, success=True, payload={"phone": phone},
                )
                return {"ok": True, "channel": channel}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            record_alert_event(
                source="ttl_heartbeat", level="alert", message=message,
                channel=channel, success=False, payload={"error": err},
            )
            return {"ok": False, "channel": channel, "error": err}
    else:  # generic
        payload = {"alert": "ttl_heartbeat_timeout", "msg": message}
        if cfg["token"]:
            headers["Authorization"] = "Bearer " + cfg["token"]

    req = urllib.request.Request(
        cfg["url"],
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            record_alert_event(
                source="ttl_heartbeat", level="alert", message=message,
                channel=channel, success=True, payload=payload,
            )
            return {"ok": True, "channel": channel}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        record_alert_event(
            source="ttl_heartbeat", level="alert", message=message,
            channel=channel, success=False, payload={"error": err},
        )
        return {"ok": False, "channel": channel, "error": err}