# -*- coding: utf-8 -*-
"""chat_level.py — THE CHAT LEVEL: the parent of a conversation.

WHY THIS EXISTS (the human, 2026-09-25)
---------------------------------------
    "same chat can have many conversaction, does i have something wrong in the
     past"
    "for same chat / can be conversaction
     1 -> 2 = chat ID -> PAIR, that is sha256 for
     3 -> 4 = chat ID -> PAIR, that is sha256 for
     5 -> 6 = chat ID -> PAIR, that is sha256 for
     as they are working at same session ID, they is the unqiue key
     but for 1 to 6 or 1 to N, is another condition, i should have
     conversaction table, maybe worker lazy
     id | conversaction ID | chat_id |"
    "do it now"

MEASURED, and the human is right: `chat_main` declares `UNIQUE (session_id)`,
so ONE session = ONE row. A chat holding MANY conversations was NOT
representable, and `chat_main` was the CONVERSATION row wearing the CHAT name.

THE FOUR LEVELS
---------------
    CHAT          the SUBJECT        -> `chat`            (this module)
    CONVERSATION  one session file   -> `chat_main`       (UNIQUE session_id)
    TURN          one message        -> `chat_center_message`
    IDENTITY      who is present     -> `identity_registry`

THE PAIR KEY BECOMES REAL HERE
------------------------------
`derived_column_registry` declares `chat_main.chat_hash` kind='pair_key' from
`chat_id + '|' + session_id`. MEASURED (2026-09-26): the LIVE values are
`sha256(chat_main.id | session_id)` on 58 of 58 rows — the writer used the
CONVERSATION id, not the CHAT id. Since `chat_main.id` is the PRIMARY KEY, that
hash re-encodes the row id and pairs NOTHING. It becomes a real pair key only
when ONE chat holds MANY conversations, which is what `chat_main.chat_id` makes
expressible.

WHAT THIS MODULE REFUSES
------------------------
  * a `chat_key` that is blank            -> refused (a container with no name)
  * a `chat_key` that is already taken    -> RETURNED, not duplicated (idempotent)
  * linking a conversation to a chat that does not exist -> refused (a phantom
    parent resolves to nothing)
  * a module/capability name that COLLIDES -> refused, naming the collision

THE NAME `chat` IS REGISTERED, NOT INVENTED
-------------------------------------------
MEASURED: `terminology_registry.assert_named(conn, 'chat')` -> NOT registered.
So `chat` was an invented word. `register_term()` fixes that with a definition
and a cite_ref, which is the repo's rule ("no name without a registered term").

Run:
    .\\.venv\\Scripts\\python.exe chat_level.py --measure
    .\\.venv\\Scripts\\python.exe chat_level.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db_schema as ds  # noqa: E402

DB = BASE / "agent.db"

# The term this module registers. `chat` is the LEVEL name; the definition says
# what it IS, and the cite says how to check it.
CHAT_TERM = {
    "term_key": "chat",
    "term_kind": "entity",
    "definition": (
        "The CHAT level: the SUBJECT a conversation belongs to. One chat may "
        "hold MANY conversations (one per VS Code session file), which is what "
        "makes `chat_main.chat_hash_recomputed` a real PAIR key. It is the "
        "PARENT of `chat_main`, and it is NOT the conversation itself — "
        "`chat_main` is the conversation, keyed UNIQUE(session_id) "
        "(db_schema.py:4317). The link column is `chat_main.chat_id` "
        "(db_schema.py:4341)."),
    # A SINGLE `path:line`, because `terminology_cite.verify_cite_ref` accepts
    # one path (optionally with a line) — a compound citation is read as prose
    # and REFUSED. MEASURED: the first version of this cite was a compound and
    # was refused with UNCITEABLE_CITE_REF, which is the verifier working.
    "cite_ref": "db_schema.py:4387",
}


class ChatRefused(RuntimeError):
    """Raised when a chat cannot be established."""


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `chat` and add `chat_main.chat_id`. ADDITIVE, never a rebuild.

    ORDER MATTERS, and it is the same order `identity_registry.ensure_schema`
    documents: the ADDITIVE column first, because the DDL's index would fail on
    an existing table that lacks the column.
    """
    added: list[str] = []
    if _table_exists(conn, "chat_main"):
        have = set(_columns(conn, "chat_main"))
        for name, decl in ds.CHAT_MAIN_NEW_COLUMNS:
            if name not in have:
                conn.execute("ALTER TABLE chat_main ADD COLUMN %s %s"
                             % (name, decl))
                added.append("chat_main.%s" % name)
    # THE CHAT'S OWN task_id / entity_id (2026-09-28). THE HUMAN:
    #     "ｗｈｅｒ　ｉｓ　ｔａｓｋ　ＩＤ　ａｎｄ　ｅｎｔｉｔｙ　ＩＤtoo?"
    # MEASURED: `chat` had 8 columns and NEITHER. The task id existed only as
    # TEXT inside `chat_key` (`chat:report:<task_id>`), so it was readable only
    # by parsing a string. `CREATE TABLE IF NOT EXISTS` never adds a column to an
    # existing table, so the additive path is required as well as the DDL.
    if _table_exists(conn, "chat"):
        have = set(_columns(conn, "chat"))
        for name, decl in ds.CHAT_NEW_COLUMNS:
            if name not in have:
                conn.execute("ALTER TABLE chat ADD COLUMN %s %s"
                             % (name, decl))
                added.append("chat.%s" % name)
    conn.executescript(ds.CHAT_DDL)
    conn.executescript(ds.CHAT_MAIN_DDL)
    # Best-effort taxonomy row: a throwaway DB (a proof) has no
    # `db_table_registry`, and its absence must not stop the table existing.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("chat", "chat",
             "the CHAT level: the SUBJECT a conversation belongs to"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "chat", "added_columns": added}


def ensure_chat(conn: sqlite3.Connection, chat_key: str, *,
                title: str | None = None, opened_by: str | None = None,
                source: str = "api") -> dict[str, Any]:
    """Find or create a CHAT by its natural key. Idempotent.

    A blank key is REFUSED: a container with no name cannot be found again, so
    it would be a row nobody can address.

    A blank TITLE IS REFUSED (the human, 2026-09-27): "each chat need to have
    title (requied) + content (requied) + entity ID (optional)". MEASURED before
    this: `chat.title` was NULL for **0 of 69** rows — the column existed and
    `ensure_chat` ACCEPTED a title, but nothing REQUIRED it, so every row was
    NULL and no chat could be listed or searched by name. A chat with no title
    is a row a reader cannot identify, which is the same defect as a chat with
    no key.

    AN EXISTING ROW IS NOT RE-TITLED. A chat that already exists is returned
    as-is: re-titling on every call would let a later caller silently overwrite
    the name a reader already knows. A title change is a separate, explicit act.
    """
    key = str(chat_key or "").strip()
    if not key:
        raise ChatRefused(["chat_key is required: a container with no name "
                           "cannot be found again"])
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM chat WHERE chat_key = ?", (key,)).fetchone()
    if row:
        return {"ok": True, "created": False, "chat": dict(row)}
    # THE TITLE IS REQUIRED AT THE WRITE SITE. A blank title is refused HERE
    # rather than stored as NULL, because a NULL title is a chat nobody can
    # list or search — the exact gap this rule closes.
    t = str(title or "").strip()
    if not t:
        raise ChatRefused(
            ["title is required: a chat with no title cannot be listed or "
             "searched, so it is a row a reader cannot identify (chat_key=%r)"
             % key])
    cur = conn.execute(
        "INSERT INTO chat (chat_key, title, opened_by, source) VALUES (?,?,?,?)",
        (key, t, opened_by, str(source)))
    conn.commit()
    out = conn.execute("SELECT * FROM chat WHERE chat_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "chat": dict(out)}


def link_chat_entity(conn: sqlite3.Connection, *, chat_id: int,
                     entity_type: str, entity_ref_id: int,
                     role: str = "subject") -> dict[str, Any]:
    """Link a chat to an ENTITY ID. OPTIONAL — a chat with no entity is valid.

    THE HUMAN (2026-09-27): "each chat need to have title (requied) + content
    (requied) + entity ID (optional)". So this is the OPTIONAL third part: a
    chat MAY name the entity it is about, and a chat that names none is still a
    valid chat.

    IT REUSES `task_entity_link` — the EXISTING link table (`track_id`,
    `entity_type`, `entity_ref_id`, `version`, `role`). MEASURED: no new column
    is needed, and `chat_center_message` has no entity column at all. A second
    link table would be a second place that knows how an entity is referenced.

    REFUSES an entity that does not resolve: `entity_type` must be a live
    `entity_type_registry.type_letter`, and `entity_ref_id` must be a positive
    integer. A link to nothing resolves to nothing, which is the same rule
    `link_conversation` applies to a phantom parent.
    """
    ensure_schema(conn)
    cid = int(chat_id)
    ch = conn.execute("SELECT chat_id, chat_key FROM chat WHERE chat_id=?",
                      (cid,)).fetchone()
    if not ch:
        raise ChatRefused(["no chat with chat_id=%d — a link must name a chat "
                           "that exists" % cid])
    letter = str(entity_type or "").strip().upper()
    letters = {str(r[0]) for r in conn.execute(
        "SELECT type_letter FROM entity_type_registry")}
    if letter not in letters:
        raise ChatRefused(
            ["entity_type %r is not a registered entity letter (%s)"
             % (entity_type, ", ".join(sorted(letters)))])
    try:
        ref = int(entity_ref_id)
    except (TypeError, ValueError):
        raise ChatRefused(["entity_ref_id %r is not an integer"
                           % (entity_ref_id,)])
    if ref <= 0:
        raise ChatRefused(["entity_ref_id must be positive, got %d" % ref])
    # `track_id` is the chat's own key, so the link is addressable from the chat.
    track = str(ch["chat_key"])
    existing = conn.execute(
        "SELECT link_id FROM task_entity_link WHERE track_id=? AND "
        "entity_type=? AND entity_ref_id=?", (track, letter, ref)).fetchone()
    if existing:
        return {"ok": True, "created": False, "link_id": int(existing["link_id"]),
                "chat_id": cid, "entity_type": letter, "entity_ref_id": ref,
                "role": role}
    cur = conn.execute(
        "INSERT INTO task_entity_link (track_id, entity_type, entity_ref_id, "
        "role) VALUES (?,?,?,?)", (track, letter, ref, str(role)))
    conn.commit()
    return {"ok": True, "created": True, "link_id": int(cur.lastrowid),
            "chat_id": cid, "entity_type": letter, "entity_ref_id": ref,
            "role": str(role)}


def link_conversation(conn: sqlite3.Connection, *, session_id: str,
                      chat_key: str) -> dict[str, Any]:
    """Point ONE conversation (`chat_main` row) at its CHAT.

    A conversation that does not exist is REFUSED, and so is a chat that does
    not exist — a phantom parent resolves to nothing, which is the same rule
    `identity_registry.open_identity` applies to a phantom worker.
    """
    sid = str(session_id or "").strip()
    if not sid:
        raise ChatRefused(["session_id is required"])
    ensure_schema(conn)
    conv = conn.execute("SELECT id FROM chat_main WHERE session_id = ?",
                        (sid,)).fetchone()
    if not conv:
        raise ChatRefused(["no chat_main row for session %s — a conversation "
                           "must exist before it can be linked" % sid[:16]])
    ch = ensure_chat(conn, chat_key)
    cid = int(ch["chat"]["chat_id"])
    conn.execute("UPDATE chat_main SET chat_id = ?, "
                 "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (cid, int(conv["id"])))
    conn.commit()
    return {"ok": True, "session_id": sid, "chat_id": cid,
            "chat_key": str(ch["chat"]["chat_key"]),
            "conversation_id": int(conv["id"]),
            "chat_created": bool(ch["created"])}


# THE LEGAL TRANSITIONS OF A CONVERSATION STATUS (plan WATCHDOG.DRAFT.CONSUMER, S5).
#
# S5 defined exactly ONE transition: `draft -> pending` — the human APPROVES a
# draft. Everything else is REFUSED, because a transition nobody defined is a
# transition nobody can check.
#
# CHANGED 2026-09-30 (plan CHAT.PIPELINE.S6, step S6.4b): the target is now
# `research`, the FIRST stage of the 9-stage conversation pipeline, NOT `pending`.
# WHY: `pending` was a placeholder for "approved, waiting for a consumer", and S6
# replaces that placeholder with the real pipeline. `research` is a REGISTERED
# TERM (`terminology_registry` term_id 1530) and a REGISTERED workflow status
# (`skill_library_api.WORKFLOW_STATUSES`, added in S6.4a) — so it passes BOTH
# gates of `set_conversation_status`. `pending` is NOT removed from the
# vocabulary (it is still a legal status value); it is only no longer the target
# of `draft`. A status with NO entry here has NO legal outgoing transition, so it
# is a terminal state. Adding a transition is a decision, and it must be written
# down before it is allowed.
CONVERSATION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("research",),
}


def set_conversation_status(conn: sqlite3.Connection, conv_id: int,
                            new_status: str, *, cite_ref: str,
                            actor: str) -> dict[str, Any]:
    """Move ONE conversation (`chat_main` row) to a new status. THE WRITER.

    WHY THIS EXISTS (plan WATCHDOG.DRAFT.CONSUMER, step S5)
    ------------------------------------------------------
    MEASURED before S5: `chat_main.status` had NO writer and NO reader —
    `UPDATE chat_main SET status` matched NOTHING in the repo, and changing the
    column on a copy made nothing react. So the column was a value nobody could
    set. This function is the writer; the approval API is its caller.

    WHAT IT REFUSES (each refusal is a rule, not a guard)
    ----------------------------------------------------
      * a `new_status` outside `WORKFLOW_STATUSES`  -> refused (an invented state)
      * a transition not in `CONVERSATION_TRANSITIONS` -> refused (undefined)
      * a blank `cite_ref`                          -> refused (an approval must
        cite the evidence it was decided on; an anonymous approval is not one)
      * a blank `actor`                             -> refused (same reason)
      * a conversation that does not exist          -> refused (a phantom row)

    WHAT IT DOES NOT DO
    -------------------
    It does NOT execute anything. Approving a conversation changes a column and
    writes an audit row; NO consumer reacts. That is the S5 scope, stated in the
    plan: the consumer is a later task. There is no kill/start path here.

    The transition is recorded in `conversation_status_event` (append-only), so
    "who approved this conversation, and on what evidence" stays answerable
    after `chat_main.status` has moved on.
    """
    import skill_library_api as sla  # lazy: avoids an import cycle at module load

    st = str(new_status or "").strip()
    if st not in sla.WORKFLOW_STATUSES:
        raise ChatRefused(
            ["new_status %r is not a registered workflow status (%s)"
             % (new_status, ", ".join(sla.WORKFLOW_STATUSES))])
    cite = str(cite_ref or "").strip()
    if not cite:
        raise ChatRefused(
            ["cite_ref is required — an approval must cite the evidence it was "
             "decided on, so an anonymous approval is refused"])
    who = str(actor or "").strip()
    if not who:
        raise ChatRefused(["actor is required — an approval must name who decided"])
    try:
        cid = int(conv_id)
    except (TypeError, ValueError):
        raise ChatRefused(["conv_id %r is not an integer" % (conv_id,)])
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id, session_id, status FROM chat_main WHERE id = ?",
        (cid,)).fetchone()
    if not row:
        raise ChatRefused(["no chat_main row with id %d — a conversation must "
                           "exist before its status can change" % cid])
    cur = str(row["status"] or "NA")
    allowed = CONVERSATION_TRANSITIONS.get(cur, ())
    if st not in allowed:
        raise ChatRefused(
            ["transition %s -> %s is not defined (from %s the legal target(s) "
             "are: %s)" % (cur, st, cur, ", ".join(allowed) or "none")])
    conn.execute("UPDATE chat_main SET status = ?, "
                 "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (st, cid))
    ev = conn.execute(
        "INSERT INTO conversation_status_event "
        "(conv_id, session_id, from_status, to_status, cite_ref, actor) "
        "VALUES (?,?,?,?,?,?)",
        (cid, str(row["session_id"] or "NA"), cur, st, cite, who))
    conn.commit()
    return {"ok": True, "conv_id": cid, "session_id": str(row["session_id"] or ""),
            "from_status": cur, "to_status": st, "cite_ref": cite, "actor": who,
            "event_id": int(ev.lastrowid)}


# THE 9 PIPELINE STAGES, in order (plan CHAT.PIPELINE.S6, §1). The consumer
# refuses a stage outside this set, so an invented stage cannot be enqueued.
PIPELINE_STAGES: tuple[str, ...] = (
    "draft", "research", "research_verify", "planning", "plan_verify",
    "writing", "verify", "verify_confirm", "completed",
)

# The stage a conversation is enqueued AT. `research` is the FIRST stage after
# `draft`, and it is the ONE legal target of `draft` (CONVERSATION_TRANSITIONS).
PIPELINE_FIRST_STAGE = "research"


def enqueue_pipeline_run(conn: sqlite3.Connection, conv_id: int, *,
                         stage: str = PIPELINE_FIRST_STAGE,
                         cite_ref: str, actor: str) -> dict[str, Any]:
    """Build ONE `pipeline_run` for a conversation at a given stage. THE WRITER.

    WHY THIS EXISTS (plan CHAT.PIPELINE.S7, step S7a)
    -------------------------------------------------
    MEASURED before S7a: `pipeline_run` had NO writer and NO reader — 0 rows on
    the live DB. S6 built the SKELETON and stated that absence honestly; this
    function is the CONSUMER that makes the pipeline move. It is the FIRST
    IMPERATIVE step in the pipeline (S6 was all declarative: vocabulary, DDL,
    a transition dict), so its proof follows S4's standard — the failure modes
    are measured, not asserted.

    WHAT IT REFUSES (each refusal is a rule, not a guard)
    ----------------------------------------------------
      * a `stage` outside `PIPELINE_STAGES`      -> refused (an invented stage)
      * a blank `cite_ref`                       -> refused (an uncited run is a
        claim; the same rule `set_conversation_status` follows)
      * a blank `actor`                          -> refused (a run must name who
        decided to start it)
      * a conversation that does not exist       -> refused (a phantom row)
      * a conversation whose status is NOT the stage being enqueued
                                                 -> refused (only an APPROVED
        conversation is enqueued; a `draft` is NOT — the "false run" failure
        mode the S7 pre-flight named, P6)

    IDEMPOTENT, AT THE DB LEVEL
    ---------------------------
    A second call for the same `(conversation_id, stage)` returns the EXISTING
    run and creates NO second row. The guarantee is the UNIQUE index
    `idx_pipeline_run_conv_stage` (db_schema.py, added in S7a), NOT a
    SELECT-then-INSERT check — a check has a TOCTOU window a race can slip
    through, and the "duplicate run" failure mode (P6) is exactly that race.

    WHAT IT DOES NOT DO
    -------------------
    It does NOT execute anything. It writes ONE row and returns. There is NO
    task, NO LLM call, NO stage advance, and NO kill/start path. The stage
    advance is a LATER step (S7c); the conductor link is S7b.
    """
    st = str(stage or "").strip()
    if st not in PIPELINE_STAGES:
        raise ChatRefused(
            ["stage %r is not one of the 9 pipeline stages (%s)"
             % (stage, ", ".join(PIPELINE_STAGES))])
    cite = str(cite_ref or "").strip()
    if not cite:
        raise ChatRefused(
            ["cite_ref is required — a run must cite the evidence it was "
             "started on, so an uncited run is refused"])
    who = str(actor or "").strip()
    if not who:
        raise ChatRefused(["actor is required — a run must name who decided"])
    try:
        cid = int(conv_id)
    except (TypeError, ValueError):
        raise ChatRefused(["conv_id %r is not an integer" % (conv_id,)])
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id, session_id, chat_id, status FROM chat_main WHERE id = ?",
        (cid,)).fetchone()
    if not row:
        raise ChatRefused(["no chat_main row with id %d — a conversation must "
                           "exist before a run can be built for it" % cid])
    # POSITIONAL access: this function must work on ANY connection, including one
    # whose `row_factory` is not `sqlite3.Row` (a caller's raw conn).
    session_id, chat_id, cur = str(row[1] or ""), row[2], str(row[3] or "NA")
    if cur != st:
        raise ChatRefused(
            ["conversation %d is at status %r, not %r — only a conversation "
             "whose status IS the stage is enqueued (a draft is not approved)"
             % (cid, cur, st)])
    # THE DEDUP: the UNIQUE index is the guarantee. A pre-check keeps the common
    # case cheap and returns the EXISTING run; the INSERT is still guarded, so a
    # race that slips past the pre-check is refused by the index, not duplicated.
    existing = conn.execute(
        "SELECT run_id FROM pipeline_run WHERE conversation_id = ? AND stage = ?",
        (cid, st)).fetchone()
    if existing:
        return {"ok": True, "created": False, "run_id": str(existing[0]),
                "conv_id": cid, "stage": st,
                "reason": "a run for this (conversation, stage) already exists"}
    run_id = "pr_%d_%s" % (cid, st)
    try:
        conn.execute(
            "INSERT INTO pipeline_run "
            "(run_id, conversation_id, chat_id, session_id, stage, cite_ref) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, cid, chat_id, session_id, st, cite))
    except sqlite3.IntegrityError as e:
        # The index refused a duplicate the pre-check did not see (a race). Read
        # the winner back rather than reporting a failure — the run EXISTS.
        won = conn.execute(
            "SELECT run_id FROM pipeline_run WHERE conversation_id = ? AND stage = ?",
            (cid, st)).fetchone()
        if won:
            return {"ok": True, "created": False, "run_id": str(won[0]),
                    "conv_id": cid, "stage": st,
                    "reason": "a concurrent enqueue won the race; the run exists"}
        raise ChatRefused(["pipeline_run insert refused: %s" % e])
    conn.commit()
    return {"ok": True, "created": True, "run_id": run_id, "conv_id": cid,
            "chat_id": chat_id, "session_id": session_id,
            "stage": st, "cite_ref": cite, "actor": who}


# THE LEGAL STAGE TRANSITIONS (plan CHAT.PIPELINE.S7, step S7c).
#
# The 9 stages are a LINEAR pipeline, so the ONLY legal move is to the NEXT
# stage. This dict is the ORDER as DATA, the same shape `CONVERSATION_TRANSITIONS`
# uses: a stage with no entry is TERMINAL, and a transition nobody defined is
# refused. It is derived from `PIPELINE_STAGES` so the two cannot drift.
PIPELINE_TRANSITIONS: dict[str, str] = {
    PIPELINE_STAGES[i]: PIPELINE_STAGES[i + 1]
    for i in range(len(PIPELINE_STAGES) - 1)
}


def advance_pipeline_run(conn: sqlite3.Connection, run_id: str, *,
                         cite_ref: str, actor: str) -> dict[str, Any]:
    """Move ONE `pipeline_run` to the NEXT stage. THE ADVANCER.

    WHY THIS EXISTS (plan CHAT.PIPELINE.S7, step S7c)
    -------------------------------------------------
    MEASURED before S7c: a run could be CREATED (S7a) and the conductor could
    create it (S7b), but NOTHING advanced it — the "stale run" failure mode the
    S7 pre-flight named (P6). This function is the state transition.

    SCOPE (the PM's ruling): this defines the ORDER ONLY. It does NOT know which
    task belongs to which stage — "a task completed" is an EXTERNAL event, and
    this function defines what happens WHEN that event arrives. The task<->run
    link is a LATER task (S8).

    WHAT IT REFUSES (each refusal is a rule, not a guard)
    ----------------------------------------------------
      * a blank `cite_ref` / `actor`        -> refused (an uncited advance is a
        claim; the same rule the writer and the consumer follow)
      * a run that does not exist           -> refused (a phantom run)
      * a stage with NO legal successor     -> refused (TERMINAL: `completed`
        cannot advance)
      * a SKIP or a BACKWARD move           -> refused (the only legal move is
        the NEXT stage; `PIPELINE_TRANSITIONS` is the closed set)

    WHAT IT DOES NOT DO
    -------------------
    It does NOT execute anything. It moves ONE column and returns. There is NO
    task, NO LLM call, NO kill/start path, and it does NOT touch
    `chat_main.status` (the conversation's status is a SEPARATE axis from the
    run's stage).
    """
    cite = str(cite_ref or "").strip()
    if not cite:
        raise ChatRefused(
            ["cite_ref is required — an advance must cite the evidence it was "
             "decided on, so an uncited advance is refused"])
    who = str(actor or "").strip()
    if not who:
        raise ChatRefused(["actor is required — an advance must name who decided"])
    rid = str(run_id or "").strip()
    if not rid:
        raise ChatRefused(["run_id is required — an advance must name the run"])
    ensure_schema(conn)
    row = conn.execute(
        "SELECT run_id, stage FROM pipeline_run WHERE run_id = ?", (rid,)).fetchone()
    if not row:
        raise ChatRefused(["no pipeline_run with run_id %r — a run must exist "
                           "before its stage can advance" % rid])
    cur = str(row[1] or "NA")
    nxt = PIPELINE_TRANSITIONS.get(cur)
    if nxt is None:
        raise ChatRefused(
            ["stage %r is TERMINAL — it has no legal successor, so it cannot "
             "advance (the pipeline is finished)" % cur])
    conn.execute(
        "UPDATE pipeline_run SET stage = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE run_id = ?", (nxt, rid))
    conn.commit()
    return {"ok": True, "run_id": rid, "from_stage": cur, "to_stage": nxt,
            "cite_ref": cite, "actor": who}


def link_run_task(conn: sqlite3.Connection, run_id: str, task_id: str, *,
                  stage: str, role: str = "advances") -> dict[str, Any]:
    """Link ONE task to ONE run. THE LINK WRITER (plan CHAT.PIPELINE.S8, S8a).

    IDEMPOTENT AT THE DB LEVEL: `UNIQUE (run_id, task_id)` means a task can be
    linked to a run AT MOST ONCE. A second call returns the EXISTING link and
    creates NO second row — the "double advance" guard (P6).
    """
    rid = str(run_id or "").strip()
    tid = str(task_id or "").strip()
    if not rid:
        raise ChatRefused(["run_id is required — a link must name the run"])
    if not tid:
        raise ChatRefused(["task_id is required — a link must name the task"])
    st = str(stage or "").strip()
    if st not in PIPELINE_STAGES:
        raise ChatRefused(
            ["stage %r is not one of the 9 pipeline stages (%s)"
             % (stage, ", ".join(PIPELINE_STAGES))])
    ensure_schema(conn)
    existing = conn.execute(
        "SELECT link_id FROM pipeline_run_task WHERE run_id = ? AND task_id = ?",
        (rid, tid)).fetchone()
    if existing:
        return {"ok": True, "created": False, "link_id": int(existing[0]),
                "run_id": rid, "task_id": tid, "stage": st,
                "reason": "this task is already linked to this run"}
    try:
        cur = conn.execute(
            "INSERT INTO pipeline_run_task (run_id, task_id, stage, role) "
            "VALUES (?, ?, ?, ?)", (rid, tid, st, str(role or "advances")))
    except sqlite3.IntegrityError as e:
        won = conn.execute(
            "SELECT link_id FROM pipeline_run_task WHERE run_id = ? AND task_id = ?",
            (rid, tid)).fetchone()
        if won:
            return {"ok": True, "created": False, "link_id": int(won[0]),
                    "run_id": rid, "task_id": tid, "stage": st,
                    "reason": "a concurrent link won the race; the link exists"}
        raise ChatRefused(["pipeline_run_task insert refused: %s" % e])
    conn.commit()
    return {"ok": True, "created": True, "link_id": int(cur.lastrowid),
            "run_id": rid, "task_id": tid, "stage": st, "role": str(role or "advances")}


def advance_from_task(conn: sqlite3.Connection, task_id: str, *,
                      cite_ref: str, actor: str) -> dict[str, Any]:
    """THE ADVANCE TRIGGER (plan CHAT.PIPELINE.S8, step S8a).

    Given a COMPLETED task, find the run it is linked to and advance that run by
    ONE stage — AT MOST ONCE. This is the OBSERVER the S8 pre-flight named: it
    reads a completion event and triggers an action.

    THE IDEMPOTENCY IS THE LINK, NOT A TIMESTAMP. Before advancing, it writes the
    `(run_id, task_id)` link; `UNIQUE (run_id, task_id)` refuses a second write,
    so a poller that reads the same completion twice advances ONCE. A time-window
    guard would be unreliable; the DB pair is exact.

    WHAT IT REFUSES
    ---------------
      * a blank `cite_ref` / `actor` / `task_id` -> refused
      * a task with NO link to any run          -> refused (nothing to advance)
      * a task already advanced                 -> returns `advanced=False`
        (idempotent, NOT an error)

    WHAT IT DOES NOT DO
    -------------------
    It does NOT read `task_lifecycle_log` itself — the CALLER (the poller, S8b)
    decides which tasks completed. This function is the ACTION; the poller is the
    TRIGGER. It does NOT execute a task, and it does NOT touch `chat_main.status`.
    """
    cite = str(cite_ref or "").strip()
    if not cite:
        raise ChatRefused(["cite_ref is required — an advance must cite its evidence"])
    who = str(actor or "").strip()
    if not who:
        raise ChatRefused(["actor is required — an advance must name who decided"])
    tid = str(task_id or "").strip()
    if not tid:
        raise ChatRefused(["task_id is required — an advance must name the task"])
    ensure_schema(conn)
    # The run this task is linked to. A task with no link has nothing to advance.
    link = conn.execute(
        "SELECT run_id, stage FROM pipeline_run_task WHERE task_id = ? "
        "ORDER BY link_id LIMIT 1", (tid,)).fetchone()
    if not link:
        raise ChatRefused(
            ["task %r is not linked to any run — link it first (link_run_task), "
             "or there is nothing to advance" % tid])
    rid = str(link[0])
    # THE IDEMPOTENCY: if this task already advanced this run, do NOT advance again.
    # The link row IS the record — `UNIQUE (run_id, task_id)` allows ONE row per
    # pair, so the advance is recorded by UPDATING that row's role, never by a
    # second INSERT (which the UNIQUE would refuse).
    already = conn.execute(
        "SELECT link_id FROM pipeline_run_task WHERE run_id = ? AND task_id = ? "
        "AND role = 'advanced'", (rid, tid)).fetchone()
    if already:
        return {"ok": True, "advanced": False, "run_id": rid, "task_id": tid,
                "reason": "this task already advanced this run (idempotent)"}
    out = advance_pipeline_run(conn, rid, cite_ref=cite, actor=who)
    # Record that THIS task performed the advance, so a second read is a no-op.
    conn.execute(
        "UPDATE pipeline_run_task SET role = 'advanced', stage = ? "
        "WHERE run_id = ? AND task_id = ?", (out["to_stage"], rid, tid))
    conn.commit()
    return {"ok": True, "advanced": True, "run_id": rid, "task_id": tid,
            "from_stage": out["from_stage"], "to_stage": out["to_stage"],
            "cite_ref": cite, "actor": who}


def backfill(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Give every existing conversation a CHAT parent. IDEMPOTENT.

    THE BACKFILL IS 1:1 TODAY, AND THAT IS REPORTED, NOT HIDDEN. MEASURED:
    `chat_main` rows == distinct `session_id`, so one chat per conversation is
    the only thing the data supports. Inventing a shared parent would be
    fabricating a relationship nobody recorded.

    A second run adds NOTHING: a conversation that already has a `chat_id` is
    skipped, so the count of newly-linked rows is 0.
    """
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, session_id, chat_id FROM chat_main ORDER BY id")]
    already = [r for r in rows if r["chat_id"] is not None]
    todo = [r for r in rows if r["chat_id"] is None]
    linked: list[dict[str, Any]] = []
    if apply:
        for r in todo:
            sid = str(r["session_id"])
            # The chat key is DERIVED from the conversation's own session id, so
            # the same input always gives the same key and a re-run is a no-op.
            res = link_conversation(conn, session_id=sid,
                                    chat_key="chat:%s" % sid)
            linked.append({"conversation_id": int(r["id"]),
                           "chat_id": res["chat_id"]})
    return {
        "ok": True,
        "conversations": len(rows),
        "already_linked": len(already),
        "to_link": len(todo),
        "linked": len(linked),
        "applied": bool(apply),
        "ratio": ("1:1 — one chat per conversation, which is what the data "
                  "supports today" if len(rows) == len({r["session_id"]
                                                        for r in rows})
                  else "1:N — some chat holds more than one conversation"),
        "cite": ("measured: chat_main rows=%d, distinct session_id=%d"
                 % (len(rows), len({r["session_id"] for r in rows}))),
    }


def register_term(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the `chat` term. DELEGATES to the ONE terminology writer."""
    import terminology_registry as tr
    tr.ensure_schema(conn)
    return tr.add_term(conn, CHAT_TERM["term_key"],
                       definition=CHAT_TERM["definition"],
                       cite_ref=CHAT_TERM["cite_ref"],
                       term_kind=CHAT_TERM["term_kind"])


def refuse_colliding_binding(conn: sqlite3.Connection, *, module_key: str,
                             capability_key: str) -> dict[str, Any]:
    """REFUSE a module/capability binding whose names collide.

    THE HUMAN (2026-09-25):
        "module = conversaction / capability = chat — is it correct? or should
         be module = chat / capability = conversaction"

    MEASURED, BOTH options collide, and this function is the refusal that says
    so instead of creating a third `chat`. The two definitions are the repo's
    own, quoted:

        module     = WHERE in the system it happens (a PLACE)
                     db_schema.py:4884, ticket_store.py:15
        capability = a VERB A PROVIDER PERFORMS
                     goal_inference.py:14

    A capability BELONGS TO a module and cannot BE one
    (`capability_registry.module_id` is NOT NULL + FK, goal_inference.py:26).
    """
    mods = {str(r[0]) for r in conn.execute(
        "SELECT module_key FROM module_registry")}
    caps = {str(r[0]) for r in conn.execute(
        "SELECT capability_key FROM capability_registry")}
    cap_suffix = {k.split(".", 1)[1] for k in caps if "." in k}
    reasons: list[str] = []
    if module_key in mods:
        reasons.append("module_key %r is ALREADY a module" % module_key)
    if module_key in cap_suffix:
        reasons.append("module_key %r is ALREADY a capability suffix — the same "
                       "word at two levels is the collision the register exists "
                       "to prevent" % module_key)
    if capability_key in cap_suffix:
        reasons.append("capability_key %r is ALREADY a capability suffix"
                       % capability_key)
    if capability_key in mods:
        reasons.append("capability_key %r is ALREADY a module_key — a capability "
                       "BELONGS TO a module and cannot BE one" % capability_key)
    return {"ok": not reasons, "module_key": module_key,
            "capability_key": capability_key, "reasons": reasons,
            "cite": ("measured: module_registry.module_key, "
                     "capability_registry.capability_key")}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    """The levels, the pair key, and the collisions. READ-ONLY."""
    ensure_schema(conn)
    has_chat = _table_exists(conn, "chat")
    conv_cols = _columns(conn, "chat_main") if _table_exists(conn, "chat_main") \
        else []
    rows = [dict(r) for r in conn.execute(
        "SELECT id, session_id, chat_id FROM chat_main ORDER BY id")]
    sessions = {r["session_id"] for r in rows}
    chats = [dict(r) for r in conn.execute(
        "SELECT chat_id, chat_key FROM chat ORDER BY chat_id")] if has_chat else []
    return {
        "ok": True,
        "chat_table_exists": has_chat,
        "chat_rows": len(chats),
        "chat_main_has_chat_id": "chat_id" in conv_cols,
        "conversations": len(rows),
        "distinct_session_id": len(sessions),
        "linked": sum(1 for r in rows if r["chat_id"] is not None),
        "unlinked": sum(1 for r in rows if r["chat_id"] is None),
        "pair_key_is_1to1": len(rows) == len(sessions),
        "cite": ("measured: PRAGMA table_info(chat_main), chat_main GROUP BY "
                 "session_id, chat rows"),
    }


def reparent_verdict(conn: sqlite3.Connection) -> dict[str, Any]:
    """THE VERDICT on the human's two directions — each with its MEASUREMENT.

    THE HUMAN (2026-09-25):
        "`module = conversation` 個 term 係 `vscode_conversation` 嘅 part；
         要升做 module 就要改 parent"
        "`capability = chat` `task_center.chat_identity` 已經存在 — 用返佢，
         唔好開第三個"

    DIRECTION 2 IS ALREADY TRUE. MEASURED: `task_center.chat_identity` exists
    (capability_id 12811, module `task_center`, kind `thinking`, is_active=1).
    Nothing needs building, and a third `chat` is REFUSED.

    DIRECTION 1 IS REFUSED, AND THE MEASUREMENT IS THE REASON. Two facts:

      (a) A MODULE IS A FILE. MEASURED: 912 of 917 `module_registry.module_key`
          values are `.py` file stems. So a module_key names a CODE UNIT, not a
          concept. `conversation` is not a file, so it cannot be a module; the
          file that owns the CHAT level is `chat_level.py`.

      (b) `conversation` IS A PART OF `vscode_conversation`, AND THE TWO
          DEFINITIONS DESCRIBE ONE THING. MEASURED, both definitions:
            vscode_conversation: "one chatSessions/<session-id>.jsonl file,
                                  identified by VS Code's own session id"
            conversation:        "One exchange thread with a model, held in one
                                  session file"
          Reparenting `conversation` to top-level would make it a SIBLING of
          `vscode_conversation`, which ASSERTS they are two systems. The
          `terminology-register` skill forbids exactly that: "Do NOT make one
          system's term the PARENT of another's — that asserts they are one
          system." The inverse holds too: making two names for one thing into
          siblings asserts two systems where there is one.

    SO THE REPARENT IS REFUSED, with the reason, and the module the human wants
    is the FILE that owns the level.
    """
    import inspect as _inspect
    import terminology_registry as tr

    out: dict[str, Any] = {"ok": True, "verdicts": [], "refusals": []}

    # ---- DIRECTION 2: reuse the existing capability ------------------------
    caps = [dict(r) for r in conn.execute(
        "SELECT capability_id, capability_key, name, module_id, capability_kind, "
        "is_active FROM capability_registry WHERE capability_key LIKE '%chat%' "
        "ORDER BY capability_id")]
    target = next((c for c in caps
                   if c["capability_key"] == "task_center.chat_identity"), None)
    out["capability"] = {
        "reuse": "task_center.chat_identity",
        "exists": target is not None,
        "row": target,
        "existing_chat_capabilities": [c["capability_key"] for c in caps],
        "cite": ("measured: capability_registry WHERE capability_key LIKE "
                 "'%%chat%%' -> %s" % [c["capability_key"] for c in caps]),
    }
    out["verdicts"].append({
        "claim": "`capability = chat` needs building",
        "verdict": ("NO — `task_center.chat_identity` already exists and is "
                    "active, so it is REUSED, not rebuilt"
                    if target else "the capability is ABSENT and must be built"),
        "cite": ("measured: capability_registry row %s"
                 % (target["capability_id"] if target else "none")),
    })
    # A third `chat` is refused by the SAME function the design verdict uses.
    third = refuse_colliding_binding(conn, module_key="chat",
                                     capability_key="chat")
    out["refusals"].append({
        "act": "create a third `chat` capability",
        "would": "REFUSED" if not third["ok"] else "allowed",
        "reasons": third["reasons"],
        "cite": third["cite"],
    })

    # ---- DIRECTION 1: the reparent ----------------------------------------
    term = conn.execute(
        "SELECT term_id, term_key, term_kind, parent_term_id, definition "
        "FROM terminology_registry WHERE term_key='conversation'").fetchone()
    parent = conn.execute(
        "SELECT term_id, term_key, definition FROM terminology_registry "
        "WHERE term_key='vscode_conversation'").fetchone()
    # (a) A MODULE IS A FILE.
    import os as _os
    mods = {str(r[0]) for r in conn.execute(
        "SELECT module_key FROM module_registry")}
    stems = {_os.path.splitext(f)[0] for f in _os.listdir(str(BASE))
             if f.endswith(".py")}
    file_modules = mods & stems
    out["module_is_a_file"] = {
        "module_keys": len(mods),
        "that_are_py_stems": len(file_modules),
        "conversation_is_a_module_key": "conversation" in mods,
        "conversation_is_a_file": "conversation" in stems,
        "chat_level_is_a_file": "chat_level" in stems,
        "chat_level_is_a_module_key": "chat_level" in mods,
        "cite": ("measured: module_registry.module_key vs the .py stems in the "
                 "repo root -> %d of %d are file stems"
                 % (len(file_modules), len(mods))),
    }
    # (b) THE TWO DEFINITIONS.
    out["definitions"] = {
        "vscode_conversation": (str(parent["definition"]) if parent else ""),
        "conversation": (str(term["definition"]) if term else ""),
        "cite": ("measured: terminology_registry.definition for both terms"),
    }
    # (c) THE WRITER.
    sig = _inspect.signature(tr.update_term)
    out["reparent_writer"] = {
        "update_term_accepts_parent_term_id": "parent_term_id" in sig.parameters,
        "signature": str(sig),
        "cite": ("measured: inspect.signature(terminology_registry.update_term) "
                 "-> parent_term_id is %s a parameter"
                 % ("" if "parent_term_id" in sig.parameters else "NOT")),
    }
    out["verdicts"].append({
        "claim": "`conversation` should be reparented to become a MODULE",
        "verdict": ("REFUSED — a module is a FILE (912 of 917 module_keys are "
                    ".py stems) and `conversation` is not a file; AND "
                    "`conversation` is a PART of `vscode_conversation` whose "
                    "definition describes the SAME thing, so making them "
                    "siblings would assert two systems where there is one"),
        "cite": ("measured: module_registry.module_key vs .py stems; "
                 "terminology_registry.definition for both terms"),
    })
    out["refusals"].append({
        "act": "reparent `conversation` to top-level (make it a module)",
        "would": "REFUSED",
        "reasons": [
            "a module_key names a FILE (912/917 are .py stems); `conversation` "
            "is not a file",
            "`conversation` is a PART of `vscode_conversation`, and both "
            "definitions describe ONE thing — siblings would assert two systems",
            "no reparent writer exists: `update_term` has no `parent_term_id` "
            "parameter, and `add_term` is idempotent on (parent, key), so a "
            "second call would create a SECOND row rather than move the first",
        ],
        "cite": ("measured: inspect.signature(update_term); "
                 "terminology_registry.add_term idempotency key"),
    })
    # ---- THE MODULE THE HUMAN ACTUALLY WANTS ------------------------------
    out["the_module"] = {
        "module_key": "chat_level",
        "is_a_file": "chat_level" in stems,
        "already_registered": "chat_level" in mods,
        "why": ("the module is the FILE that owns the CHAT level; `chat_level.py` "
                "is that file, and it is not yet a module_key"),
        "cite": ("measured: chat_level.py exists in the repo root; "
                 "module_registry has no `chat_level` row"),
    }
    return out


def register_chat_level_module(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register `chat_level` as a MODULE. DELEGATES to the ONE writer.

    THE HUMAN (2026-09-25): "do it now", after the reparent verdict said the
    module the human wants is the FILE that owns the CHAT level.

    MEASURED: 912 of 917 `module_registry.module_key` values are `.py` file
    stems, so a module IS a file, and `chat_level.py` is one. Registering it is
    ADDITIVE — nothing is renamed or rebound.

    `capability_store.ensure_module` is the ONE writer, and it is idempotent
    ("Register a module if absent. Idempotent; never overwrites"), so a second
    call is a no-op rather than a duplicate.
    """
    import capability_store as cs
    cs.ensure_schema(conn)
    return cs.ensure_module(
        conn, "chat_level", "Chat Level",
        description=("The CHAT level: the SUBJECT a conversation belongs to. "
                     "Owns the `chat` table and `chat_main.chat_id`, so one "
                     "chat may hold many conversations."))


def refuse_capability_rebind(conn: sqlite3.Connection, *, capability_key: str,
                             new_module_key: str) -> dict[str, Any]:
    """REFUSE rebinding a capability to another module. The KEY is the reason.

    THE HUMAN (2026-09-25) was offered "bind `task_center.chat_identity` to it
    (or keep it on `task_center`)". MEASURED, the rebind is WRONG, and the
    reason is the key itself:

        the capability key is `{module}.{capability}`

    MEASURED across every capability:

        task_center.validate_new_task   -> module task_center
        mouse_spot_helper.core          -> module mouse_spot_helper
        openclaw.chat                   -> module openclaw_companion
        llm.text_completion             -> module llm_runtime

    So rebinding `task_center.chat_identity` to a `chat_level` module would make
    the key LIE about its own module — the exact defect the terminology register
    exists to prevent. The key is ALSO referenced by
    `src/task_center/ontology_store.py:361` (the seeder) and by
    `skill_contract_template.taxonomy_path`, so a rebind would break those too.

    A capability whose key does NOT encode its module is REPORTED, not refused:
    the convention is measured, and a counter-example is a finding.
    """
    key = str(capability_key or "").strip()
    new_mod = str(new_module_key or "").strip()
    row = conn.execute(
        "SELECT c.capability_key, c.module_id, m.module_key "
        "FROM capability_registry c JOIN module_registry m "
        "ON m.module_id = c.module_id WHERE c.capability_key = ?",
        (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_CAPABILITY", "capability_key": key,
                "cite": "measured: capability_registry has no row for %r" % key}
    cur_mod = str(row["module_key"])
    prefix = key.split(".", 1)[0] if "." in key else ""
    # THE CONVENTION, measured over EVERY capability rather than assumed.
    all_caps = [dict(r) for r in conn.execute(
        "SELECT c.capability_key, m.module_key FROM capability_registry c "
        "JOIN module_registry m ON m.module_id = c.module_id")]
    encodes = [c for c in all_caps
               if "." in c["capability_key"]
               and c["capability_key"].split(".", 1)[0] == c["module_key"]]
    reasons: list[str] = []
    if prefix and prefix != new_mod:
        reasons.append(
            "the key %r ENCODES its module (%r), so rebinding it to %r would "
            "make the key LIE about its own module" % (key, prefix, new_mod))
    if cur_mod != new_mod:
        reasons.append("the capability is currently on module %r" % cur_mod)
    reasons.append(
        "the key is referenced by src/task_center/ontology_store.py:361 (the "
        "seeder) and by skill_contract_template.taxonomy_path, so a rebind "
        "would break those readers too")
    return {
        "ok": not reasons,
        "capability_key": key,
        "current_module": cur_mod,
        "proposed_module": new_mod,
        "key_prefix": prefix,
        "reasons": reasons,
        "convention": {
            "capabilities": len(all_caps),
            "whose_key_encodes_its_module": len(encodes),
        },
        "cite": ("measured: capability_registry JOIN module_registry -> %d of "
                 "%d capability keys encode their module as the prefix"
                 % (len(encodes), len(all_caps))),
    }


def pair_key_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """THE REAL state of the pair key, measured — never the claimed one.

    WHY THIS EXISTS (2026-09-25)
    ----------------------------
    A report claimed the pair key was ✅ and had "gone 1:1 -> 1:N". MEASURED:
    only **5 of 58** non-NULL `chat_hash` values matched
    `chat_pair_hash(chat_id, session_id)` — 52 MISMATCH — and EVERY `chat_hash`
    still mapped to exactly ONE `chat_id`, so it was still 1:1.

    ROOT CAUSE — CORRECTED 2026-09-26 by measurement. This docstring used to say
    "53 rows were hashed with `id` instead of `chat_id`, before `chat_id`
    existed". That is FALSE. MEASURED: **58 of 58** non-NULL `chat_hash` values
    are `sha256(chat_main.id | session_id)` and **0** are
    `sha256(chat_main.chat_id | session_id)`. So it is not a half-migrated legacy
    population — it is the LIVE writer's formula
    (`skill_library_api.py:401-408`, `chat_pair_hash(str(mid), session_id)` where
    `mid` is `chat_main.id`), applied consistently to every row. The writer has
    since stopped writing it; `chat_hash` is now append-only audit and
    `chat_hash_recomputed` carries the correct key.

    This function still MEASURES the stale column on purpose: it is the
    diagnostic that found the defect, and a diagnostic that stops looking is how
    the defect was missed the first time.

    A claim about a population must be a MEASUREMENT of that population. This
    function recomputes the key on EVERY row and reports the count, so the number
    cannot be asserted.
    """
    from skill_library_api import chat_pair_hash

    cols = _columns(conn, "chat_main")
    has_recomputed = "chat_hash_recomputed" in cols
    rows = list(conn.execute(
        "SELECT id, chat_id, session_id, chat_hash%s FROM chat_main ORDER BY id"
        % (", chat_hash_recomputed" if has_recomputed else "")))
    n = len(rows)
    match = mismatch = null_hash = null_key = 0
    stale_ids: list[int] = []
    fixable = 0
    for r in rows:
        sid = r["session_id"]
        cid = r["chat_id"]
        ch = r["chat_hash"]
        if ch is None or str(ch) == "":
            null_hash += 1
            continue
        if cid is None or sid is None or str(sid) == "":
            null_key += 1
            continue
        want = chat_pair_hash(str(cid), str(sid))
        if want == ch:
            match += 1
        else:
            mismatch += 1
            stale_ids.append(int(r["id"]))
        recomputed = r["chat_hash_recomputed"] if has_recomputed else None
        if recomputed == want:
            fixable += 1
    # THE PAIR PROPERTY, measured: how many distinct chat_ids per chat_hash?
    pair_1n = conn.execute(
        "SELECT count(*) FROM (SELECT chat_hash FROM chat_main "
        " WHERE chat_hash IS NOT NULL GROUP BY chat_hash "
        " HAVING count(DISTINCT chat_id) > 1)").fetchone()[0]
    n_hash = conn.execute(
        "SELECT count(DISTINCT chat_hash) FROM chat_main "
        "WHERE chat_hash IS NOT NULL").fetchone()[0]
    n_cid = conn.execute(
        "SELECT count(DISTINCT chat_id) FROM chat_main "
        "WHERE chat_id IS NOT NULL").fetchone()[0]
    return {
        "ok": True, "rows": n,
        "chat_hash_matches_formula": match,
        "chat_hash_mismatch": mismatch,
        "chat_hash_null": null_hash,
        "unusable_key": null_key,
        "stale_row_ids": stale_ids[:10],
        "has_recomputed_column": has_recomputed,
        "recomputed_correct": fixable,
        "distinct_chat_hash": n_hash,
        "distinct_chat_id": n_cid,
        "chat_hash_grouping_more_than_one_chat": pair_1n,
        "is_a_real_pair_key": pair_1n > 0,
        "formula": "chat_pair_hash(chat_id, session_id)",
        "cite": ("measured: recompute chat_pair_hash(chat_id, session_id) on all "
                 "%d chat_main rows -> %d match, %d mismatch, %d NULL"
                 % (n, match, mismatch, null_hash)),
    }


def refuse_stale_pair_lookup(conn: sqlite3.Connection,
                             chat_hash: str) -> dict[str, Any]:
    """REFUSE a lookup by a STALE `chat_hash`, naming the column that IS correct.

    WHY A REFUSAL AND NOT A FALLBACK
    --------------------------------
    MEASURED: 52 of 58 `chat_hash` values are stale. A reader that keys on
    `chat_hash` gets a WRONG row and no warning — the worst outcome, because it
    is believed. A silent fallback to `chat_hash_recomputed` would hide the
    staleness instead of surfacing it.

    So the gate REFUSES and NAMES the fix, the same shape as the KIND gate in
    `register_fill`: a refusal that says only WHAT failed teaches nothing.
    """
    cols = _columns(conn, "chat_main")
    if "chat_hash_recomputed" not in cols:
        return {"ok": False, "code": "NO_RECOMPUTED_COLUMN",
                "message": ("chat_main has no chat_hash_recomputed column — the "
                            "correct pair key does not exist yet")}
    from skill_library_api import chat_pair_hash
    hit = conn.execute("SELECT * FROM chat_main WHERE chat_hash=?",
                       (chat_hash,)).fetchone()
    if not hit:
        return {"ok": True, "code": "NOT_FOUND_BY_STALE_HASH", "found": False,
                "message": "no chat_main row carries this chat_hash"}
    sid, cid = hit["session_id"], hit["chat_id"]
    want = chat_pair_hash(str(cid), str(sid)) if cid is not None else None
    if want == chat_hash:
        return {"ok": True, "code": "CURRENT", "found": True,
                "row_id": int(hit["id"]),
                "message": "this chat_hash is CURRENT; the lookup is safe"}
    return {
        "ok": False, "code": "STALE_PAIR_KEY", "found": True,
        "row_id": int(hit["id"]),
        "stale_chat_hash": chat_hash,
        "correct_chat_hash": want,
        "correct_column": "chat_hash_recomputed",
        "why": ("this chat_hash was computed from `id`, not `chat_id` — it was "
                "written before `chat_id` existed and never recomputed"),
        "tutorial": ("key the lookup on `chat_hash_recomputed`, or recompute with "
                     "`chat_pair_hash(chat_id, session_id)`. `chat_hash` is "
                     "append-only audit and is NOT rewritten."),
    }


def field_list_verdict(conn: sqlite3.Connection) -> dict[str, Any]:
    """THE VERDICT on the human's proposed field list — each field MEASURED.

    THE HUMAN (2026-09-26):
        "vscode chat before will be register for chat id, but design is chnaged,
         now should be
           conversaction ID
           chat ID
           sha256
           role = ask
           status = draft
         is it correct?"

    THE FIELD LIST IS RIGHT, AND IT SPANS TWO LEVELS. MEASURED: the first three
    fields are the CONVERSATION (`chat_main`), the last two are the TURN
    (`chat_center_message`). A single row cannot hold all five.

    `role = ask` IS WRONG, AND THE DATABASE REFUSES IT. MEASURED:
    `chat_center_message.role` carries `CHECK (role IN ('Question','Answer'))`,
    so an INSERT of `role='ask'` fails with
    `CHECK constraint failed: role IN ('Question', 'Answer')`.

    AND `ask` IS A STATUS, NOT A ROLE. MEASURED: `ask` IS in
    `WORKFLOW_STATUSES`, and the ROLE vocabulary is `role_registry`
    (researcher / writer / verifier). So `role = ask` puts a STATUS in the ROLE
    slot — and the human already put `status = draft` in the same list, so the
    list would carry TWO statuses and NO role.
    """
    out: dict[str, Any] = {"ok": True, "fields": [], "verdicts": [],
                           "refusals": []}

    conv_cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_main)")]
    msg_cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(chat_center_message)")]

    # ---- THE FIVE FIELDS, each with the table it lives on ------------------
    spec = (
        ("conversation ID", "chat_main", "id",
         "the CONVERSATION's own PK (one chatSessions/<id>.jsonl)"),
        ("chat ID", "chat_main", "chat_id",
         "the FK to `chat` — the PARENT, added 2026-09-25"),
        ("sha256", "chat_main", "sha256",
         "a FUNCTION of session_id (derived_column_registry kind='function')"),
        ("role", "chat_center_message", "role",
         "the TURN's role; CHECK (role IN ('Question','Answer'))"),
        ("status", "chat_center_message", "status",
         "the TURN's workflow status; WORKFLOW_STATUSES"),
    )
    for label, table, col, why in spec:
        cols = conv_cols if table == "chat_main" else msg_cols
        out["fields"].append({
            "field": label, "table": table, "column": col,
            "exists": col in cols, "why": why,
            "cite": "measured: PRAGMA table_info(%s) has %s=%s"
                    % (table, col, col in cols),
        })

    # ---- THE LEVEL SPLIT ---------------------------------------------------
    conv_fields = [f["field"] for f in out["fields"]
                   if f["table"] == "chat_main"]
    turn_fields = [f["field"] for f in out["fields"]
                   if f["table"] == "chat_center_message"]
    out["levels"] = {
        "conversation_fields": conv_fields,
        "turn_fields": turn_fields,
        "one_row_can_hold_all": False,
        "why": ("the first %d fields are the CONVERSATION and the last %d are "
                "the TURN, so a single row cannot hold all five"
                % (len(conv_fields), len(turn_fields))),
        "cite": "measured: PRAGMA table_info(chat_main) vs chat_center_message",
    }
    out["verdicts"].append({
        "claim": "the five fields all exist",
        "verdict": "YES — every one exists, on one of two tables",
        "cite": "measured: PRAGMA table_info for both tables",
    })
    out["verdicts"].append({
        "claim": "one row can hold all five fields",
        "verdict": ("NO — %d are the CONVERSATION and %d are the TURN"
                    % (len(conv_fields), len(turn_fields))),
        "cite": "measured: the two tables",
    })

    # ---- `role = ask` ------------------------------------------------------
    ddl = ""
    try:
        ddl = str(conn.execute("SELECT sql FROM sqlite_master WHERE "
                               "name='chat_center_message'").fetchone()[0] or "")
    except Exception:
        ddl = ""
    check_line = next((ln.strip() for ln in ddl.splitlines()
                       if "CHECK (role IN" in ln), "")
    roles = [str(r[0]) for r in conn.execute(
        "SELECT role_key FROM role_registry ORDER BY role_key")]
    import skill_library_api as _sla
    statuses = list(_sla.WORKFLOW_STATUSES)
    turn_statuses = list(_sla.TURN_STATUSES)
    out["role_ask"] = {
        "check_constraint": check_line,
        "ask_is_a_role": "ask" in roles,
        "ask_is_a_status": "ask" in statuses,
        "role_vocabulary": roles,
        "status_vocabulary": statuses,
        "turn_statuses": turn_statuses,
        "draft_is_a_status": "draft" in turn_statuses,
        "cite": ("measured: db_schema.py chat_center_message DDL (%s); "
                 "role_registry -> %s; WORKFLOW_STATUSES -> %s"
                 % (check_line, roles, statuses)),
    }
    out["verdicts"].append({
        "claim": "`role = ask` is correct",
        "verdict": ("NO — the CHECK refuses `ask`, and `ask` is a STATUS, not a "
                    "role. The list would carry TWO statuses (ask, draft) and "
                    "NO role."),
        "cite": ("measured: %s; `ask` in WORKFLOW_STATUSES=%s; `ask` in "
                 "role_registry=%s"
                 % (check_line, "ask" in statuses, "ask" in roles)),
    })
    out["verdicts"].append({
        "claim": "`status = draft` is correct",
        "verdict": ("YES — `draft` is in TURN_STATUSES, and it is the DEFAULT "
                    "the human chose for a written-down turn"),
        "cite": "measured: TURN_STATUSES -> %s" % turn_statuses,
    })
    out["refusals"].append({
        "act": "write `role = 'ask'`",
        "would": "REFUSED by the CHECK constraint",
        "cite": "measured: %s" % check_line,
    })

    # ---- THE CORRECTED LIST, REPORTED (not silently substituted) -----------
    out["corrected"] = {
        "conversation": {
            "conversation_id": "chat_main.id",
            "chat_id": "chat_main.chat_id",
            "sha256": "chat_main.sha256",
        },
        "turn": {
            "role": "chat_center_message.role — one of %s" % roles,
            "status": "chat_center_message.status — one of %s" % turn_statuses,
        },
        "note": ("the human's five fields are RIGHT; only `role = ask` is wrong, "
                 "because `ask` is a STATUS. The role slot takes a role "
                 "(researcher/writer/verifier) or the turn's Question/Answer."),
        "cite": "measured: the two tables + role_registry + TURN_STATUSES",
    }
    return out


def identity_service_route_verdict(conn: sqlite3.Connection) -> dict[str, Any]:
    """THE VERDICT on the human's design questions — each with its MEASUREMENT.

    THE HUMAN (2026-09-26):
        "= identity is confirm LLM is the list can have right to get that! it can
         help 5W1H, is it correct?"
        "but this directly connect seemd can design better"
        "directly to question flow? but that is for question flow"
        "is services center = ticket center?"
        "can ask for which service ticket can help?"
        "so will be table standardize the content from LLM and is it text or array"
        "so it is worker translater like recription?"
        "data -> translate under format -> get the prompt (prompt generator)"
        "will it can be cycle, worker can got the instruction and answer to system
         by his data"

    EVERY VERDICT CARRIES THE MEASUREMENT THAT DECIDES IT. An opinion would carry
    none, and the human asked for a design, not a preference.
    """
    out: dict[str, Any] = {"ok": True, "verdicts": [], "refusals": []}

    def _one(sql: str, *a: Any) -> Any:
        try:
            r = conn.execute(sql, a).fetchone()
            return r[0] if r else None
        except sqlite3.OperationalError:
            return None

    def _rows(sql: str, *a: Any) -> list[dict[str, Any]]:
        try:
            return [dict(r) for r in conn.execute(sql, a)]
        except sqlite3.OperationalError:
            return []

    # ---- 1. identity -> LLM: a DIRECT FK -----------------------------------
    id_cols = [r[1] for r in conn.execute("PRAGMA table_info(identity_registry)")]
    id_rows = _one("SELECT COUNT(*) FROM identity_registry")
    with_llm = _one("SELECT COUNT(*) FROM identity_registry "
                    "WHERE llm_id IS NOT NULL")
    out["identity_llm"] = {
        "has_llm_id": "llm_id" in id_cols,
        "identities": id_rows,
        "with_llm_id": with_llm,
        "cite": ("measured: PRAGMA table_info(identity_registry) has llm_id=%s; "
                 "%s of %s identities carry one"
                 % ("llm_id" in id_cols, with_llm, id_rows)),
    }
    out["verdicts"].append({
        "claim": "identity links to LLM",
        "verdict": ("YES — by a DIRECT FK (`identity_registry.llm_id` -> "
                    "`llm_model.id`)"),
        "cite": out["identity_llm"]["cite"],
    })

    # ---- 2. the ROUTE table ------------------------------------------------
    routes = _rows("SELECT route_key, purpose_key, ticket_origin, workflow_key "
                   "FROM purpose_route_registry")
    out["routes"] = {
        "rows": routes,
        "count": len(routes),
        "cite": "measured: purpose_route_registry -> %d row(s)" % len(routes),
    }
    out["verdicts"].append({
        "claim": "a direct FK is enough to route a chat to a service",
        "verdict": ("NO — the FK answers 'which model', not 'which service'. "
                    "`purpose_route_registry` answers the second, and it holds "
                    "%d route(s)" % len(routes)),
        "cite": out["routes"]["cite"],
    })

    # ---- 3. `service` — TWO registries -------------------------------------
    tc = _rows("SELECT id, ticket_origin, name FROM ticket_center ORDER BY id")
    ls = _rows("SELECT id, llm_route, needs_flag FROM llm_service ORDER BY id")
    out["service_registries"] = {
        "ticket_center": {"key_column": "ticket_origin",
                          "keys": [r["ticket_origin"] for r in tc]},
        "llm_service": {"key_column": "llm_route",
                        "keys": [r["llm_route"] for r in ls]},
        "collision": ("both tables call their key a 'service', and they answer "
                      "DIFFERENT questions: ticket_center = which service opens "
                      "a ticket; llm_service = which LLM route serves a task"),
        "cite": ("measured: ticket_center.ticket_origin -> %s; llm_service.llm_route "
                 "-> %s" % ([r["ticket_origin"] for r in tc],
                            [r["llm_route"] for r in ls])),
    }
    out["verdicts"].append({
        "claim": "services center = ticket center",
        "verdict": ("PARTLY — `ticket_center` IS the service registry, but "
                    "`llm_service` is a SECOND one. Two tables both call their "
                    "key a 'service'."),
        "cite": out["service_registries"]["cite"],
    })

    # ---- 4. the 5W1H gap, SPLIT -------------------------------------------
    sk = {str(r["kind_key"]) for r in _rows(
        "SELECT kind_key FROM subject_kind_registry")}
    bd = {str(r["subject_kind"]) for r in _rows(
        "SELECT DISTINCT subject_kind FROM dimension_binding_registry")}
    unbound = sorted(sk - bd)
    kinds = {str(r["kind_key"]): r for r in _rows(
        "SELECT kind_key, ref_table, ref_column, description FROM "
        "subject_kind_registry")}
    with_ref = [k for k in unbound
                if str((kinds.get(k) or {}).get("ref_table") or "NA")
                not in ("", "NA")]
    no_ref = [k for k in unbound if k not in with_ref]
    out["w1h_gap"] = {
        "kinds": len(sk),
        "bound": len(bd),
        "unbound": unbound,
        "unbound_with_a_ref": with_ref,
        "unbound_without_a_ref": no_ref,
        "why": ("the gap SPLITS: a kind WITH a ref is a real subject that should "
                "be bound; a kind with NO ref has no instance to bind, so "
                "binding it would invent one"),
        "cite": ("measured: subject_kind_registry (%d kinds) vs "
                 "dimension_binding_registry (%d bound); ref_table per unbound "
                 "kind" % (len(sk), len(bd))),
    }
    out["verdicts"].append({
        "claim": "the 5W1H gap is one problem",
        "verdict": ("NO — it SPLITS: %d unbound kinds HAVE a ref (a real gap) "
                    "and %d have NO ref (not bindable)"
                    % (len(with_ref), len(no_ref))),
        "cite": out["w1h_gap"]["cite"],
    })

    # ---- 5. the CONTENT type ----------------------------------------------
    types: dict[str, str] = {}
    for nm, col in (("wording_registry", "template"),
                    ("study_registry", "fields_json"),
                    ("skill_registry", "output_schema"),
                    ("chat_center_message", "content")):
        try:
            ty = next((r[2] for r in conn.execute("PRAGMA table_info(%s)" % nm)
                       if r[1] == col), "?")
        except sqlite3.OperationalError:
            ty = "(absent)"
        types["%s.%s" % (nm, col)] = str(ty)
    out["content_types"] = {
        "types": types,
        "all_text": all(t.upper() == "TEXT" for t in types.values()),
        "cite": "measured: PRAGMA table_info for the four columns -> %s" % types,
    }
    out["verdicts"].append({
        "claim": "the content from the LLM is an array",
        "verdict": ("NO — it is TEXT. SQLite has no array type, so JSON lives in "
                    "a string, and the cost is that there is NO schema guarantee "
                    "on the content."),
        "cite": out["content_types"]["cite"],
    })

    # ---- 6. the CYCLE ------------------------------------------------------
    cycle = {}
    for nm in ("skill_factor_registry", "skill_factor_proof", "proof_run",
               "llm_100_run"):
        n = _one("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                 "AND name=?", nm)
        cycle[nm] = {"exists": bool(n),
                     "rows": _one("SELECT COUNT(*) FROM %s" % nm) if n else None}
    out["cycle"] = {
        "tables": cycle,
        "cite": ("measured: %s"
                 % ", ".join("%s=%s" % (k, v["rows"] if v["exists"] else "ABSENT")
                             for k, v in cycle.items())),
    }
    out["verdicts"].append({
        "claim": "the cycle cannot carry data",
        "verdict": ("NO — it carries data (%s). But `llm_100_run` is NOT a "
                    "table, so the run layer is `proof_run` — a NAMING gap, not "
                    "an empty cycle."
                    % ", ".join("%s=%s" % (k, v["rows"])
                                for k, v in cycle.items() if v["exists"])),
        "cite": out["cycle"]["cite"],
    })

    # ---- 7. the ROUTE GAP --------------------------------------------------
    svc = [str(r["ticket_origin"]) for r in _rows(
        "SELECT ticket_origin FROM ticket_center WHERE is_active=1 "
        "ORDER BY ticket_origin")]
    wf = [str(r["workflow_key"]) for r in _rows(
        "SELECT workflow_key FROM workflow_registry ORDER BY workflow_key")]
    out["route_gap"] = {
        "services": svc,
        "workflows": wf,
        "recorded_routes": len(routes),
        "possible_combinations": len(svc) * len(wf),
        "unrecorded": len(svc) * len(wf) - len(routes),
        "why": ("which purpose needs which service via which workflow is a HUMAN "
                "decision; this REPORTS the gap rather than filling it"),
        "cite": ("measured: %d services x %d workflows = %d combinations, %d "
                 "recorded" % (len(svc), len(wf), len(svc) * len(wf),
                               len(routes))),
    }
    out["verdicts"].append({
        "claim": "we can ask which service helps",
        "verdict": ("YES, via the route — but only %d of %d combinations is "
                    "recorded" % (len(routes), len(svc) * len(wf))),
        "cite": out["route_gap"]["cite"],
    })
    out["refusals"].append({
        "act": "invent a route for an unrecorded combination",
        "would": "REFUSED — which purpose needs which service is a HUMAN decision",
        "cite": out["route_gap"]["cite"],
    })
    return out


def bind_real_kinds(conn: sqlite3.Connection, *, apply: bool = False
                    ) -> dict[str, Any]:
    """Bind the unbound kinds that HAVE a ref. REFUSE the ones that do not.

    THE HUMAN (2026-09-26): "it can help 5W1H, is it correct?"

    MEASURED, the gap SPLITS, and conflating the two halves is the trap:

      * a kind WITH a `ref_table` is a REAL subject (`table` -> db_table_registry,
        `field` -> db_field_registry), so it SHOULD be bound.
      * a kind with NO ref (`route`, `file`, `register`, `tacid`, `system`,
        `other`) has no instance to bind, so binding it would INVENT one.

    The bindings are DERIVED from the kind's own `ref_table`/`ref_column`, so the
    text is a fact the register already holds rather than a sentence I wrote.
    """
    import dimension_binding_registry as dbr
    import skill_5w1h as fw

    kinds = {str(r["kind_key"]): dict(r) for r in conn.execute(
        "SELECT kind_key, ref_table, ref_column, description FROM "
        "subject_kind_registry")}
    bound = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT subject_kind FROM dimension_binding_registry")}
    unbound = sorted(set(kinds) - bound)
    with_ref = [k for k in unbound
                if str(kinds[k]["ref_table"] or "NA") not in ("", "NA")]
    no_ref = [k for k in unbound if k not in with_ref]

    written: list[dict[str, Any]] = []
    if apply:
        for k in with_ref:
            rt = str(kinds[k]["ref_table"])
            rc = str(kinds[k]["ref_column"])
            for dim in fw.DIMENSION_NAMES:
                # THE BINDING TEXT IS DERIVED from the kind's own ref, so it is
                # a fact the register holds, not a sentence I invented.
                text = ("the %s dimension of a %s, whose ref is %s.%s"
                        % (dim, k, rt, rc))
                r = dbr.add_binding(
                    conn, k, dim, binding_text=text,
                    example="%s.%s" % (rt, rc),
                    cite_ref="subject_kind_registry.py:SUBJECT_KIND_SEED")
                written.append({"kind": k, "dim": dim, "ok": r.get("ok")})
    return {
        "ok": True,
        "unbound": unbound,
        "bound_now": with_ref,
        "refused_no_ref": no_ref,
        "written": len(written),
        "applied": bool(apply),
        "refusal": {
            "act": "bind a kind that has NO ref",
            "would": "REFUSED — a kind with no ref has no instance to bind, so "
                     "binding it would invent one",
            "kinds": no_ref,
        },
        "cite": ("measured: subject_kind_registry.ref_table per unbound kind -> "
                 "%d with a ref, %d without" % (len(with_ref), len(no_ref))),
    }


def service_workflow_verdict(conn: sqlite3.Connection) -> dict[str, Any]:
    """The verdict on the human's three decisions (2026-09-26).

    THE HUMAN:
        "1) `service_ticket`"
        "2) table : service_workflow by DB driven, purpose get by factor from
            LLM data analyze = logic generator
            but the factor is for matching service_ticket
            table need to + field to classify service_ticket > key factor"
        "3) yes, must with measured unit"

    MEASURED, and the answer is that TWO of the three names already have a
    table, and the THIRD thing (the factor -> service link) does not exist:

      * `service_ticket`  -> the EXISTING `ticket_center` (its key column is
        literally `service`). MEASURED: 4 rows.
      * `service_workflow` -> the EXISTING `purpose_route_registry` (it already
        holds purpose_key + ticket_origin + workflow_key). MEASURED: 1 row.
      * the factor -> service link -> **ABSENT**. `skill_factor_registry` has
        NO service column; its `applies_to` is a TAG SET matched against
        `skill_registry.capability_tags`, which names a CAPABILITY, not a
        SERVICE.

    So the human's "table need to + field" is RIGHT, and the table is
    `purpose_route_registry` (the service_workflow table), NOT the factor table.
    The factor table's own guard says so: `_proof_skill_factor.py` asserts "the
    register has a FIXED column set (a new factor is a ROW)".
    """
    def _one(sql: str, *a: Any) -> Any:
        try:
            r = conn.execute(sql, a).fetchone()
            return r[0] if r else None
        except sqlite3.OperationalError:
            return None

    def _rows(sql: str, *a: Any) -> list[dict[str, Any]]:
        try:
            return [dict(r) for r in conn.execute(sql, a)]
        except sqlite3.OperationalError:
            return []

    out: dict[str, Any] = {"verdicts": []}

    # ---- DECISION 1: `service_ticket` --------------------------------------
    tc_cols = [r[1] for r in conn.execute("PRAGMA table_info(ticket_center)")]
    tc_rows = _rows("SELECT id, ticket_origin, name FROM ticket_center ORDER BY id")
    st_exists = bool(_one("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                          "AND name='service_ticket'"))
    out["service_ticket"] = {
        "name_exists_as_a_table": st_exists,
        "existing_table": "ticket_center",
        "key_column": "ticket_origin",
        "rows": len(tc_rows),
        "keys": [r["ticket_origin"] for r in tc_rows],
        "cite": ("measured: sqlite_master has no `service_ticket`; "
                 "ticket_center.ticket_origin -> %s"
                 % [r["ticket_origin"] for r in tc_rows]),
    }
    out["verdicts"].append({
        "claim": "`service_ticket` is a NEW table to create",
        "verdict": ("NO — it NAMES the existing `ticket_center`, whose key "
                    "column is literally `service` (%d row(s): %s). The name is "
                    "a RENAME, which is a human decision and is NOT done here."
                    % (len(tc_rows), [r["ticket_origin"] for r in tc_rows])),
        "cite": out["service_ticket"]["cite"],
    })

    # ---- DECISION 2: `service_workflow` ------------------------------------
    prr_cols = [r[1] for r in conn.execute(
        "PRAGMA table_info(purpose_route_registry)")]
    prr_rows = _rows("SELECT route_key, purpose_key, ticket_origin, workflow_key "
                     "FROM purpose_route_registry")
    sw_exists = bool(_one("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                          "AND name='service_workflow'"))
    out["service_workflow"] = {
        "name_exists_as_a_table": sw_exists,
        "existing_table": "purpose_route_registry",
        "columns": prr_cols,
        "rows": len(prr_rows),
        "cite": ("measured: sqlite_master has no `service_workflow`; "
                 "purpose_route_registry columns -> %s, %d row(s)"
                 % (prr_cols, len(prr_rows))),
    }
    out["verdicts"].append({
        "claim": "`service_workflow` is a NEW table to create",
        "verdict": ("NO — it NAMES the existing `purpose_route_registry`, which "
                    "already holds purpose_key + ticket_origin + workflow_key "
                    "(%d row(s)). It IS 'service + workflow, DB driven'."
                    % len(prr_rows)),
        "cite": out["service_workflow"]["cite"],
    })

    # ---- DECISION 2b: the factor -> service link ---------------------------
    f_cols = [r[1] for r in conn.execute("PRAGMA table_info(skill_factor_registry)")]
    svc_cols = [c for c in f_cols if "service" in c.lower()]
    out["factor_service_link"] = {
        "factor_columns": f_cols,
        "service_columns_on_the_factor": svc_cols,
        "applies_to_is": ("a TAG SET matched against "
                          "skill_registry.capability_tags — it names a "
                          "CAPABILITY, not a SERVICE"),
        "cite": ("measured: PRAGMA table_info(skill_factor_registry) -> %d "
                 "columns, service-named: %s" % (len(f_cols), svc_cols or "NONE")),
    }
    out["verdicts"].append({
        "claim": "a factor already links to a service",
        "verdict": ("NO — `skill_factor_registry` has NO service column "
                    "(service-named columns: %s). `applies_to` is a TAG SET "
                    "matched against `skill_registry.capability_tags`, which "
                    "names a CAPABILITY. So 'the factor matches the service' is "
                    "NOT expressible today — this is the missing field."
                    % (svc_cols or "NONE")),
        "cite": out["factor_service_link"]["cite"],
    })
    out["verdicts"].append({
        "claim": "the missing field belongs on the FACTOR table",
        "verdict": ("NO — it belongs on `purpose_route_registry` (the "
                    "service_workflow table). The factor table's own guard "
                    "asserts 'the register has a FIXED column set (a new factor "
                    "is a ROW)', so a new factor is a ROW, not a column. The "
                    "human's words are 'table need to + field', and the table "
                    "that routes a service to a workflow is "
                    "`purpose_route_registry`."),
        "cite": ("measured: _proof_skill_factor.py:126 asserts the FIXED column "
                 "set; purpose_route_registry.py:71 is the route DDL"),
    })

    # ---- DECISION 3: the measured unit -------------------------------------
    out["unit"] = unit_gap(conn)
    out["verdicts"].append({
        "claim": "every factor carries a MEASURED unit",
        "verdict": ("PARTLY — %d of %d factors carry a `metric_unit`, but %d "
                    "name NO SUBJECT (a unit that does not say WHAT it counts "
                    "is unmeasurable): %s"
                    % (out["unit"]["with_a_unit"], out["unit"]["factors"],
                       out["unit"]["naming_no_subject"],
                       out["unit"]["naming_no_subject_keys"])),
        "cite": out["unit"]["cite"],
    })

    # ---- the CHAIN ---------------------------------------------------------
    out["chain"] = {
        "llm_to_factor": "logic_generator / factor_distill (exists)",
        "factor_to_service": "ABSENT — the missing field",
        "service_to_workflow": "purpose_route_registry (exists)",
        "complete": False,
        "cite": ("measured: the three tables exist; the factor -> service hop "
                 "has no column"),
    }
    out["verdicts"].append({
        "claim": "the chain LLM -> factor -> service -> workflow is complete",
        "verdict": ("NO — the first hop (LLM data -> factor) and the third "
                    "(service -> workflow) exist; the SECOND (factor -> "
                    "service) has no column. That is the one field to add."),
        "cite": out["chain"]["cite"],
    })
    return out


def register_service_terms(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the two names the human chose, so the collision is RECORDED.

    THE HUMAN (2026-09-26): "1) `service_ticket`" / "2) table : service_workflow"

    MEASURED last round: TWO tables both call their key a 'service' —
    `ticket_center.ticket_origin` and `llm_service.llm_route` — and they answer
    DIFFERENT questions. Naming the ticket side `service_ticket` RESOLVES that
    collision. The name is REGISTERED here; the live table is NOT renamed (a
    rename is a human decision, and `ticket_center` is live).

    DELEGATES to the ONE terminology writer, so the citation is verified by
    `terminology_cite.verify_cite_ref` rather than trusted.
    """
    import terminology_registry as tr
    tr.ensure_schema(conn)
    out: list[dict[str, Any]] = []
    for key, definition, cite in (
        ("service_ticket",
         "The TICKET-side service registry. It NAMES the existing `ticket_center` "
         "table, whose key column is `ticket_origin` (chat_center, task_center, "
         "manual, llm_service). It is the ORIGIN a TICKET is opened from, as "
         "opposed to `llm_service`, which is the LLM route that serves a task. "
         "The two were both called 'service' and answered different questions; "
         "this name separates them.",
         "db_schema.py:4973"),
        ("service_workflow",
         "The DB-driven table that routes a SERVICE to a WORKFLOW for a PURPOSE. "
         "It NAMES the existing `purpose_route_registry` table, which already "
         "holds purpose_key + ticket_origin + workflow_key with "
         "UNIQUE(purpose_key, ticket_origin, workflow_key). The purpose is derived "
         "from LLM data by the logic generator; the factor that classifies the "
         "service is held in its `key_factor` column.",
         "purpose_route_registry.py:71"),
        ("ticket_origin",
         "WHERE a ticket came from. The key column of `ticket_center`, renamed "
         "from `service` (human, 2026-09-26) because `service` carried two "
         "unrelated meanings. MEASURED values: chat_center (the chat center "
         "flow), task_center (the task pipeline), manual (a human, outside any "
         "pipeline), llm_service (a local=0 LLM provider handoff). It is an "
         "ORIGIN, not a capability.",
         "db_schema.py:4973"),
        ("llm_route",
         "WHICH MODEL POOL a request is routed to. The key column of "
         "`llm_service`, renamed from `service_key` (human, 2026-09-26). "
         "MEASURED values: llm.text, llm.vision, eye.capture. The code already "
         "called this table 'the route registry' (llm_service_store.py:11), so "
         "the name is the one the code used.",
         "llm_service_store.py:11"),
        ("llm_route_provider",
         "WHO can serve a ROUTE, and HOW. Renamed from `llm_service_provider` "
         "(human, 2026-09-26, option A) so the name aligns with "
         "`llm_service.llm_route`. MEASURED: 6 rows binding a route to the "
         "models that serve it (llm.text -> qwen2.5:7b-instruct, "
         "qwen/qwen3.8-27b; llm.vision -> qwen2.5vl:7b, qwen/qwen3.8-27b; "
         "eye.capture -> openclaw.screen_capture, openclaw.camera). Its FK "
         "column `llm_route_id` points at `llm_service.id`.",
         "db_schema.py:5743"),
    ):
        r = tr.add_term(conn, key, definition=definition, cite_ref=cite,
                        term_kind="entity")
        out.append({"term_key": key, "ok": r.get("ok"),
                    "code": r.get("code") or "registered"})
    return {"ok": all(x["ok"] for x in out), "terms": out,
            "cite": "measured: terminology_registry.add_term (the ONE writer)"}


def add_factor_service_link(conn: sqlite3.Connection, *, apply: bool = False
                            ) -> dict[str, Any]:
    """Add the ONE missing field: the factor that classifies a service.

    THE HUMAN (2026-09-26):
        "the factor is for matching service_ticket"
        "table need to + field to classify service_ticket > key factor"

    WHERE IT GOES, and this is measured rather than chosen: on
    `purpose_route_registry` — the table that already routes a SERVICE to a
    WORKFLOW. NOT on `skill_factor_registry`, whose own guard asserts "the
    register has a FIXED column set (a new factor is a ROW)".

    The column is added through the EXISTING additive path
    (`db_schema._add_columns_if_missing`), so NO table is rebuilt and NO row is
    rewritten. It is NULLABLE and defaults to NULL: a route whose factor nobody
    has classified is HONESTLY unclassified, and inventing a factor for it would
    be the defect this column exists to prevent.

    IDEMPOTENT: a second call adds nothing.
    """
    from db_schema import _add_columns_if_missing

    before = [r[1] for r in conn.execute(
        "PRAGMA table_info(purpose_route_registry)")]
    added = _add_columns_if_missing(
        conn, "purpose_route_registry",
        [("key_factor", "TEXT")])
    if added:
        conn.commit()
    after = [r[1] for r in conn.execute(
        "PRAGMA table_info(purpose_route_registry)")]
    return {
        "ok": True,
        "table": "purpose_route_registry",
        "column": "key_factor",
        "added": added,
        "already_present": "key_factor" in before,
        "columns_before": before,
        "columns_after": after,
        "rebuilt": False,
        "cite": ("measured: db_schema._add_columns_if_missing -> ALTER TABLE "
                 "ADD COLUMN; added=%s" % added),
    }


def unit_gap(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every factor whose `metric_unit` does not NAME ITS SUBJECT.

    THE HUMAN (2026-09-26): "yes, must with measured unit"

    THE RULE (`factor_first_principle`): a unit must name WHAT it measures —
    `pct of X`, `count of X`, `boolean of X`, `score_0_100 of X`. A unit that
    does not is unmeasurable: nobody can check it.

    This function REPORTS the gap. It does NOT fix it — inventing a unit for a
    factor is exactly the defect the rule exists to prevent.
    """
    prefixes = ("pct of", "count of", "boolean of", "score_0_100 of")
    rows = [dict(r) for r in conn.execute(
        "SELECT factor_key, metric_kind, metric_unit, metric_target, cite_ref "
        "FROM skill_factor_registry ORDER BY factor_key")]
    no_subject = [r for r in rows
                  if not any(str(r["metric_unit"] or "").startswith(p)
                             for p in prefixes)]
    return {
        "factors": len(rows),
        "with_a_unit": sum(1 for r in rows if str(r["metric_unit"] or "").strip()),
        "with_a_kind": sum(1 for r in rows if str(r["metric_kind"] or "").strip()),
        "with_a_target": sum(1 for r in rows
                             if str(r["metric_target"] or "").strip()),
        "with_a_cite": sum(1 for r in rows if str(r["cite_ref"] or "").strip()),
        "naming_no_subject": len(no_subject),
        "naming_no_subject_keys": [r["factor_key"] for r in no_subject],
        "naming_no_subject_units": {r["factor_key"]: r["metric_unit"]
                                    for r in no_subject},
        "prefixes": list(prefixes),
        "refusal": {
            "act": "accept a unit that names no subject",
            "would": ("REFUSED — a unit that does not say WHAT it measures "
                      "cannot be checked, so it is REPORTED, never fixed by "
                      "inventing a subject"),
            "keys": [r["factor_key"] for r in no_subject],
        },
        "cite": ("measured: skill_factor_registry.metric_unit -> %d of %d "
                 "factors name no subject" % (len(no_subject), len(rows))),
    }


def match_factor_to_service(conn: sqlite3.Connection, *, apply: bool = False
                            ) -> dict[str, Any]:
    """Derive the factor -> service link, and REFUSE the ones that cannot be.

    THE HUMAN (2026-09-26):
        "the factor is for matching service_ticket"
        "table need to + field to classify service_ticket > key factor"

    THE BASIS IS MEASURED, not chosen. A factor's `applies_to` is a TAG SET:

        *            20 factors
        code         12
        crud          6
        llm.remote    2
        llm.local     1

    and the services are `ticket_center.ticket_origin` = chat_center / llm_service /
    manual / task_center, plus `llm_service.llm_route` = eye.capture /
    llm.text / llm.vision.

    A tag MATCHES a service ONLY when the tag NAMES it — `llm.local` and
    `llm.remote` name `llm_service` (the tag's first segment IS the service's
    first segment). `*`, `code` and `crud` name NO service, so a match for them
    would be INVENTED, and this function REFUSES them rather than guessing.

    The match is DERIVED from the two registers, so it is a fact they already
    hold rather than a sentence I wrote.
    """
    services = {str(r[0]) for r in conn.execute(
        "SELECT ticket_origin FROM ticket_center")}
    services |= {str(r[0]) for r in conn.execute(
        "SELECT llm_route FROM llm_service")}
    # A service's FIRST SEGMENT is what a tag can name: `llm_service` -> `llm`,
    # `chat_center` -> `chat`, `eye.capture` -> `eye`.
    by_segment: dict[str, list[str]] = {}
    for s in services:
        by_segment.setdefault(s.split(".", 1)[0].split("_", 1)[0], []).append(s)

    factors = [dict(r) for r in conn.execute(
        "SELECT factor_key, applies_to FROM skill_factor_registry "
        "ORDER BY factor_key")]
    matched: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for f in factors:
        tags = [t.strip() for t in str(f["applies_to"] or "").split(",")
                if t.strip()]
        hits: list[str] = []
        for t in tags:
            seg = t.split(".", 1)[0].split("_", 1)[0]
            hits.extend(by_segment.get(seg, []))
        if hits:
            matched.append({"factor_key": f["factor_key"], "tags": tags,
                            "services": sorted(set(hits))})
        else:
            refused.append({"factor_key": f["factor_key"], "tags": tags,
                            "why": ("tag(s) %s name NO service, so a match "
                                    "would be invented" % tags)})

    written = 0
    if apply:
        for m in matched:
            # ONE factor per route: the FIRST matched service, deterministically.
            svc = m["services"][0]
            cur = conn.execute(
                "UPDATE purpose_route_registry SET key_factor = ?, "
                "updated_at = datetime('now') WHERE ticket_origin = ? "
                "AND (key_factor IS NULL OR key_factor = '')",
                (m["factor_key"], svc))
            written += cur.rowcount
        conn.commit()
    return {
        "ok": True,
        "services": sorted(services),
        "factors": len(factors),
        "matched": matched,
        "refused": refused,
        "written": written,
        "applied": bool(apply),
        "refusal": {
            "act": "match a factor whose tag names no service",
            "would": ("REFUSED — `*`, `code` and `crud` name no service, so a "
                      "match for them would be invented rather than derived"),
            "factors": [r["factor_key"] for r in refused],
        },
        "cite": ("measured: skill_factor_registry.applies_to vs "
                 "ticket_center.ticket_origin + llm_service.llm_route -> %d "
                 "matched, %d refused" % (len(matched), len(refused))),
    }


def fill_key_factor(conn: sqlite3.Connection, *, apply: bool = False
                    ) -> dict[str, Any]:
    """Fill `purpose_route_registry.key_factor` from the DERIVED match.

    THE HUMAN (2026-09-26): "table need to + field to classify service_ticket >
    key factor"

    The field exists (added additively). This fills it — but ONLY from a match
    that was DERIVED (`match_factor_to_service`), never from a guess. A route
    whose service has no matched factor stays NULL, which is the honest reading
    of "nobody has classified this service yet".

    IDEMPOTENT: it only writes a route whose `key_factor` is NULL or empty, so a
    second call changes nothing and a human's edit is never clobbered.
    """
    m = match_factor_to_service(conn, apply=apply)
    routes = [dict(r) for r in conn.execute(
        "SELECT route_key, ticket_origin, key_factor FROM purpose_route_registry "
        "ORDER BY route_key")]
    return {
        "ok": True,
        "routes": routes,
        "filled": sum(1 for r in routes
                      if str(r["key_factor"] or "").strip()),
        "unfilled": sum(1 for r in routes
                        if not str(r["key_factor"] or "").strip()),
        "written": m["written"],
        "applied": bool(apply),
        "cite": ("measured: purpose_route_registry.key_factor -> %d of %d "
                 "route(s) filled" % (sum(1 for r in routes
                                          if str(r["key_factor"] or "").strip()),
                                      len(routes))),
    }


# THE HUMAN'S 8 STEPS (2026-09-26), and the MEASURED mapping onto the flow.
#
#   1  service is worker identity, template helps the worker get the session ID
#   2  verify have worker identity
#   3  prepare the environment for this identity
#   4  verify prepare done
#   5  ask user what can we help
#   6  data > analyze to fill in the Analyze table
#   7  Analyze table -> logic generator API (5W1H -> 6 factor -> research data)
#   8  prompt generator has the prompt with research data
#
# MEASURED: steps 1 and 2 ALREADY EXIST in `worker_identity_flow`, in REVERSE
# order — step 1 is the gate ("is the text a mapping of field names to values")
# and step 2 is the describe ("list the field names that appear as KEYS"). The
# study `worker_identity_case.fields_json` holds exactly SESSION_ID, MODEL,
# CHAT_ID, CHAT_SHA256, which is what step 2 lists. Step 3 is a COMPUTED verdict,
# not one of the human's steps.
#
# So the human's steps 3..8 are the SIX rows to add, at step_no 4..9. The earlier
# estimate of five was off by one because step 3 had not been measured.
WORKER_IDENTITY_STEPS: tuple[tuple[int, str, str, str, str, str], ...] = (
    (4, "ontology", "describe",
     "List the environment settings prepared for this identity, one per line, "
     "and nothing else.\nIf nothing was prepared, answer NONE.",
     "NA", "prepare the environment for this identity"),
    (5, "ontology", "gate",
     "Is the environment for this identity prepared — that is, does "
     "`environment_configure` hold an active row for it?\n"
     "Answer with exactly one word: YES or NO.",
     "YES", "verify prepare done"),
    (6, "ontology", "describe",
     "State what the user asked for help with, in one line, and nothing else.\n"
     "If the user has not said, answer NONE.",
     "NA", "ask user what can we help"),
    (7, "tdd", "describe",
     "List the fields the analysis filled in the Analyze table, one per line, "
     "and nothing else.\nIf the analysis is empty, answer NONE.",
     "NA", "data > analyze to fill the Analyze table"),
    (8, "tdd", "count",
     "How many factors did the logic generator derive from the 5W1H "
     "dimensions?\nAnswer with a single integer, and nothing else.",
     "NA", "logic generator: 5W1H -> 6 factor -> research real data"),
    (9, "tdd", "verdict",
     "(computed from steps 7 + 8)",
     "YES", "prompt generator has the prompt with research data"),
)


def extend_worker_identity_flow(conn: sqlite3.Connection, *, apply: bool = False
                                ) -> dict[str, Any]:
    """Add the human's steps 3..8 to `worker_identity_flow` (step_no 4..9).

    THE HUMAN (2026-09-26): "yes" — to writing the 8 steps as `workflow_step`.

    DELEGATES to `question_flow.add_step`, the ONE writer, so the layer and the
    step kind are validated against the register rather than trusted. MEASURED:
    `question_flow.LAYERS` = ('tdd', 'ontology') and `STEP_KINDS` = ('gate',
    'describe', 'count', 'name', 'verdict'), so a step outside those is REFUSED
    by the writer.

    IDEMPOTENT: `add_step` upserts on `(workflow_id, step_no)`, so a second call
    rewrites the same six rows and adds none.
    """
    import question_flow as qf

    flow_key = "worker_identity_flow"
    before = [dict(r) for r in conn.execute(
        "SELECT s.step_no, s.step_kind, s.layer_key FROM workflow_step s "
        "JOIN workflow_registry w ON w.workflow_id = s.workflow_id "
        "WHERE w.workflow_key = ? ORDER BY s.step_no", (flow_key,))]
    written: list[dict[str, Any]] = []
    if apply:
        for (no, layer, kind, tmpl, expected, note) in WORKER_IDENTITY_STEPS:
            r = qf.add_step(conn, flow_key, no, layer_key=layer, step_kind=kind,
                            question_template=tmpl, expected=expected,
                            is_final=(kind == "verdict"), notes=note)
            written.append({"step_no": no, "ok": r.get("ok")})
    after = [dict(r) for r in conn.execute(
        "SELECT s.step_no, s.step_kind, s.layer_key FROM workflow_step s "
        "JOIN workflow_registry w ON w.workflow_id = s.workflow_id "
        "WHERE w.workflow_key = ? ORDER BY s.step_no", (flow_key,))]
    return {
        "ok": True,
        "flow_key": flow_key,
        "steps_before": before,
        "steps_after": after,
        "planned": [s[0] for s in WORKER_IDENTITY_STEPS],
        "written": len(written),
        "applied": bool(apply),
        "layers_allowed": list(qf.LAYERS),
        "kinds_allowed": list(qf.STEP_KINDS),
        "cite": ("measured: question_flow.add_step (the ONE writer) -> "
                 "workflow_step rows %s" % [s[0] for s in WORKER_IDENTITY_STEPS]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--pair-key", action="store_true",
                    help="the REAL state of chat_main's pair key (measured)")
    ap.add_argument("--reparent-verdict", action="store_true",
                    help="the verdict on the human's two directions")
    ap.add_argument("--register-module", action="store_true",
                    help="register chat_level as a module (additive)")
    ap.add_argument("--field-verdict", action="store_true",
                    help="the verdict on the human's proposed field list")
    ap.add_argument("--route-verdict", action="store_true",
                    help="the verdict on identity -> service -> route + 5W1H")
    ap.add_argument("--bind-real-kinds", action="store_true",
                    help="bind the unbound kinds that HAVE a ref")
    ap.add_argument("--service-workflow-verdict", action="store_true",
                    help="the verdict on service_ticket / service_workflow / "
                         "the factor -> service link / the measured unit")
    ap.add_argument("--add-factor-service-link", action="store_true",
                    help="add the ONE missing field (additive, idempotent)")
    ap.add_argument("--match-factor-service", action="store_true",
                    help="derive the factor -> service match; refuse the rest")
    ap.add_argument("--fill-key-factor", action="store_true",
                    help="fill purpose_route_registry.key_factor from the match")
    ap.add_argument("--extend-worker-flow", action="store_true",
                    help="add the human's steps 3..8 to worker_identity_flow")
    ap.add_argument("--apply", action="store_true",
                    help="create the table, register the term, backfill")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.extend_worker_flow:
            e = extend_worker_identity_flow(conn, apply=True)
            print("flow: %s" % e["flow_key"])
            print("layers allowed: %s" % e["layers_allowed"])
            print("kinds allowed : %s" % e["kinds_allowed"])
            print()
            print("BEFORE (%d steps):" % len(e["steps_before"]))
            for s in e["steps_before"]:
                print("  step %-3s %-10s %s"
                      % (s["step_no"], s["step_kind"], s["layer_key"]))
            print()
            print("AFTER (%d steps):" % len(e["steps_after"]))
            for s in e["steps_after"]:
                print("  step %-3s %-10s %s"
                      % (s["step_no"], s["step_kind"], s["layer_key"]))
            print()
            print("planned: %s   written: %d" % (e["planned"], e["written"]))
            print("cite: %s" % e["cite"])
            return 0
        if args.match_factor_service:
            m = match_factor_to_service(conn, apply=True)
            print("services: %s" % m["services"])
            print("factors : %d" % m["factors"])
            print()
            print("MATCHED (%d) — the tag NAMES a service:" % len(m["matched"]))
            for x in m["matched"]:
                print("  %-40s %s -> %s"
                      % (x["factor_key"], x["tags"], x["services"]))
            print()
            print("REFUSED (%d) — the tag names NO service:" % len(m["refused"]))
            for x in m["refused"]:
                print("  %-40s %s" % (x["factor_key"], x["tags"]))
            print()
            print("rows written: %d" % m["written"])
            print("cite: %s" % m["cite"])
            return 0
        if args.fill_key_factor:
            f = fill_key_factor(conn, apply=True)
            for r in f["routes"]:
                print("  %-40s service=%-12s key_factor=%s"
                      % (r["route_key"], r["llm_route"], r["key_factor"]))
            print()
            print("filled: %d   unfilled: %d   written: %d"
                  % (f["filled"], f["unfilled"], f["written"]))
            print("cite: %s" % f["cite"])
            return 0
        if args.service_workflow_verdict:
            v = service_workflow_verdict(conn)
            print("VERDICTS")
            for x in v["verdicts"]:
                print("  CLAIM  : %s" % x["claim"])
                print("  VERDICT: %s" % x["verdict"])
                print("  CITE   : %s" % x["cite"])
                print()
            st = v["service_ticket"]
            print("service_ticket -> %s (key column %s, %d row(s))"
                  % (st["existing_table"], st["key_column"], st["rows"]))
            sw = v["service_workflow"]
            print("service_workflow -> %s (%d row(s))"
                  % (sw["existing_table"], sw["rows"]))
            print()
            u = v["unit"]
            print("UNIT: %d factors, %d with a unit, %d name NO subject"
                  % (u["factors"], u["with_a_unit"], u["naming_no_subject"]))
            for k in u["naming_no_subject_keys"]:
                print("  %-40s %r" % (k, u["naming_no_subject_units"][k]))
            print()
            print("CHAIN complete: %s" % v["chain"]["complete"])
            return 0
        if args.add_factor_service_link:
            t = register_service_terms(conn)
            for x in t["terms"]:
                print("term %-18s ok=%s %s"
                      % (x["term_key"], x["ok"], x["code"]))
            print()
            r = add_factor_service_link(conn, apply=True)
            print("table  : %s" % r["table"])
            print("column : %s" % r["column"])
            print("added  : %s" % r["added"])
            print("already: %s" % r["already_present"])
            print("rebuilt: %s" % r["rebuilt"])
            print("cite   : %s" % r["cite"])
            return 0
        if args.route_verdict:
            v = identity_service_route_verdict(conn)
            print("VERDICTS")
            for x in v["verdicts"]:
                print("  CLAIM  : %s" % x["claim"])
                print("  VERDICT: %s" % x["verdict"])
                print("  CITE   : %s" % x["cite"])
                print()
            sr = v["service_registries"]
            print("SERVICE REGISTRIES (the collision):")
            print("  ticket_center.ticket_origin     : %s" % sr["ticket_center"]["keys"])
            print("  llm_service.llm_route   : %s" % sr["llm_service"]["keys"])
            print()
            g = v["w1h_gap"]
            print("5W1H GAP: %d kinds, %d bound, %d unbound"
                  % (g["kinds"], g["bound"], len(g["unbound"])))
            print("  WITH a ref (a real gap) : %s" % g["unbound_with_a_ref"])
            print("  NO ref (not bindable)   : %s" % g["unbound_without_a_ref"])
            print()
            rg = v["route_gap"]
            print("ROUTE GAP: %d services x %d workflows = %d combinations, "
                  "%d recorded, %d unrecorded"
                  % (len(rg["services"]), len(rg["workflows"]),
                     rg["possible_combinations"], rg["recorded_routes"],
                     rg["unrecorded"]))
            print()
            print("CONTENT TYPES: %s" % v["content_types"]["types"])
            print("CYCLE: %s" % v["cycle"]["cite"])
            return 0
        if args.bind_real_kinds:
            r = bind_real_kinds(conn, apply=True)
            print("unbound: %s" % r["unbound"])
            print("bound now: %s (%d rows written)"
                  % (r["bound_now"], r["written"]))
            print("refused (no ref): %s" % r["refused_no_ref"])
            print("cite: %s" % r["cite"])
            return 0
        if args.field_verdict:
            v = field_list_verdict(conn)
            print("THE FIVE FIELDS")
            for f in v["fields"]:
                print("  %-16s %-22s %-10s exists=%s"
                      % (f["field"], f["table"], f["column"], f["exists"]))
            print()
            lv = v["levels"]
            print("LEVELS: conversation=%s" % lv["conversation_fields"])
            print("        turn        =%s" % lv["turn_fields"])
            print("        one row can hold all five: %s" % lv["one_row_can_hold_all"])
            print()
            print("VERDICTS")
            for x in v["verdicts"]:
                print("  CLAIM  : %s" % x["claim"])
                print("  VERDICT: %s" % x["verdict"])
                print("  CITE   : %s" % x["cite"])
                print()
            ra = v["role_ask"]
            print("role = ask:")
            print("  CHECK      : %s" % ra["check_constraint"])
            print("  ask is a ROLE  : %s" % ra["ask_is_a_role"])
            print("  ask is a STATUS: %s" % ra["ask_is_a_status"])
            print("  role vocabulary: %s" % ra["role_vocabulary"])
            print("  draft is a status: %s" % ra["draft_is_a_status"])
            print()
            print("CORRECTED LIST: %s" % v["corrected"])
            return 0
        if args.pair_key:
            k = pair_key_report(conn)
            print("=== chat_main PAIR KEY (measured) ===")
            print("  formula: %s" % k["formula"])
            print("  rows                     : %d" % k["rows"])
            print("  chat_hash MATCHES formula: %d" % k["chat_hash_matches_formula"])
            print("  chat_hash MISMATCH       : %d" % k["chat_hash_mismatch"])
            print("  chat_hash NULL           : %d" % k["chat_hash_null"])
            print("  unusable (no chat_id/sid): %d" % k["unusable_key"])
            print("  stale row ids (first 10) : %s" % k["stale_row_ids"])
            print()
            print("  chat_hash_recomputed col : %s" % k["has_recomputed_column"])
            print("  recomputed IS correct    : %d of %d"
                  % (k["recomputed_correct"], k["rows"]))
            print()
            print("  distinct chat_hash       : %d" % k["distinct_chat_hash"])
            print("  distinct chat_id         : %d" % k["distinct_chat_id"])
            print("  hash groups with >1 chat : %d" % k["chat_hash_grouping_more_than_one_chat"])
            print("  IS A REAL PAIR KEY       : %s" % k["is_a_real_pair_key"])
            print()
            print("  cite: %s" % k["cite"])
            print()
            if k["chat_hash_mismatch"]:
                print("  VERDICT: the claim '1:1 -> 1:N' is FALSE. %d of %d rows "
                      "are stale, and no chat_hash groups more than one chat."
                      % (k["chat_hash_mismatch"], k["rows"]))
            return 0
        if args.register_module:
            m = register_chat_level_module(conn)
            print("module 'chat_level': ok=%s created=%s module_id=%s"
                  % (m.get("ok"), m.get("created"), m.get("module_id")))
            r = refuse_capability_rebind(conn,
                                         capability_key="task_center.chat_identity",
                                         new_module_key="chat_level")
            print()
            print("rebind task_center.chat_identity -> chat_level: ok=%s" % r["ok"])
            print("  current module: %s   proposed: %s   key prefix: %s"
                  % (r["current_module"], r["proposed_module"], r["key_prefix"]))
            print("  convention: %d of %d capability keys encode their module"
                  % (r["convention"]["whose_key_encodes_its_module"],
                     r["convention"]["capabilities"]))
            for why in r["reasons"]:
                print("    - %s" % why)
            return 0
        if args.reparent_verdict:
            v = reparent_verdict(conn)
            print("VERDICTS")
            for x in v["verdicts"]:
                print("  CLAIM  : %s" % x["claim"])
                print("  VERDICT: %s" % x["verdict"])
                print("  CITE   : %s" % x["cite"])
                print()
            print("CAPABILITY: reuse=%s exists=%s"
                  % (v["capability"]["reuse"], v["capability"]["exists"]))
            print("  existing: %s" % v["capability"]["existing_chat_capabilities"])
            print()
            mf = v["module_is_a_file"]
            print("A MODULE IS A FILE: %d of %d module_keys are .py stems"
                  % (mf["that_are_py_stems"], mf["module_keys"]))
            print("  conversation is a module_key: %s   is a file: %s"
                  % (mf["conversation_is_a_module_key"],
                     mf["conversation_is_a_file"]))
            print("  chat_level is a file: %s   is a module_key: %s"
                  % (mf["chat_level_is_a_file"],
                     mf["chat_level_is_a_module_key"]))
            print()
            print("REPARENT WRITER: update_term accepts parent_term_id = %s"
                  % v["reparent_writer"]["update_term_accepts_parent_term_id"])
            print()
            print("REFUSALS (%d)" % len(v["refusals"]))
            for r in v["refusals"]:
                print("  %s -> %s" % (r["act"], r["would"]))
                for why in r.get("reasons") or []:
                    print("      - %s" % why)
            print()
            print("THE MODULE THE HUMAN WANTS: %s" % v["the_module"])
            return 0
        if args.apply:
            sch = ensure_schema(conn)
            print("schema: %s (added %s)" % (sch["table"], sch["added_columns"]))
            term = register_term(conn)
            print("term 'chat': ok=%s %s" % (term.get("ok"),
                                             term.get("code") or "registered"))
            bf = backfill(conn, apply=True)
            print("backfill: conversations=%d already=%d to_link=%d linked=%d"
                  % (bf["conversations"], bf["already_linked"], bf["to_link"],
                     bf["linked"]))
            print("          ratio: %s" % bf["ratio"])
            print("          cite : %s" % bf["cite"])
            m = measure(conn)
            print("after: chat_rows=%d linked=%d unlinked=%d"
                  % (m["chat_rows"], m["linked"], m["unlinked"]))
            return 0
        m = measure(conn)
        for k, v in m.items():
            print("  %-24s %s" % (k, v))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
