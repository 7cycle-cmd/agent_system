# -*- coding: utf-8 -*-
"""identity_registry.py — THE IDENTITY SYSTEM. 5W1H, `who` is COMPOSITE.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "identity is 5W1H, who = session ID + worker ID required
     what = get workflow id
     workflow tell you how to have chat ID"
    "problem is worker is unqiue system, and identity is another system"
    "you need to have 2 table unqiue"

So an identity is NOT a session id alone. `who` is COMPOSITE — a session AND a
worker — and `what` is the WORKFLOW being run. The chain is:

    session_id + worker_id  ->  workflow_id  ->  chat_id

`chat_id` IS THE OUTPUT, NOT AN INPUT. It does not exist until the workflow
produces it ("workflow tell you how to have chat ID"), so the column is NULLABLE
and NULL means "not yet produced" — a real state. A NOT NULL would force a
fabricated value at insert time, which is the "fake" the user warned about.

MEASURED: the workflow that yields `chat_id` ALREADY EXISTS —
`flow_setting.flow_key='chat_center_identity'`:

    step 1  question='who a u?'                                value='worker_identity'
    step 2  question='confirm the identity block you received'  value='worker_identity_confirm'

Step 1 IS the `who` question and step 2 IS the two-way confirm. This module
REUSES that workflow; it does not build a second one.

WHY THE TWO SYSTEMS ARE SEPARATE
--------------------------------
    WORKER   = WHO DOES THE WORK. Exists whether or not anyone is logged in.
    IDENTITY = WHO IS PRESENT. Exists whether or not any work is being done.

A worker can act in many identities; one identity can host many workers. Merging
them would make "which worker did this" and "which session was this" the same
question — the mixing the user has rejected ("don't mix up").

The JOIN is NOT a column here. It is a 5W1H binding
(`worker_identity_binding.py`), so the same worker can bind to an identity
differently per dimension, and the binding is DATA.

WHAT THIS MODULE REFUSES
------------------------
  * a blank `session_id`           -> refused (`who` part 1 is required)
  * a `worker_id` that is not a registered worker -> refused (a phantom worker
    resolves to nothing)
  * a `workflow_id` that does not exist -> refused
  * a blank `cite_ref`             -> refused ("no citation, no finding")
  * a `chat_id` supplied at CREATE time -> refused. It is the workflow's OUTPUT;
    accepting it here would let a caller assert a chat that was never produced.

CLI
---
    python identity_registry.py --list
    python identity_registry.py --get <identity_key>
    python identity_registry.py --open --session <sid> --worker <key> --workflow <id>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"


class IdentityRefused(RuntimeError):
    """Raised when an identity would be stored without a real who/what."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("identity_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `identity_registry` and register it in `db_table_registry`."""
    import db_schema as ds

    conn.execute("PRAGMA foreign_keys = ON;")
    # ADDITIVE columns FIRST, before the DDL. `CREATE TABLE IF NOT EXISTS` never
    # adds a column to an existing table (the defect that left the live
    # `llm_model` without `model_id`), AND the DDL's `CREATE INDEX ... (task_id)`
    # would fail on an existing table that lacks the column. So the migration
    # must land BEFORE the script, and be guarded on the table already existing.
    for name, decl in getattr(ds, "identity_registry_NEW_COLUMNS", ()):
        have = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_registry'"
        ).fetchone()
        if not have:
            break
        cols = {r[1] for r in conn.execute("PRAGMA table_info(identity_registry)")}
        if name not in cols:
            conn.execute("ALTER TABLE identity_registry ADD COLUMN %s %s"
                         % (name, decl))
    # 🔴 `ds.identity_registry_DDL` DOES NOT EXIST. MEASURED 2026-09-29: the real
    # name is `IDENTITY_registry_DDL` (`db_schema.py`). The lowercase spelling
    # appears NOWHERE in `db_schema.py`, so this line raised `AttributeError` on
    # EVERY call to `ensure_schema`.
    conn.executescript(ds.IDENTITY_registry_DDL)
    # Best-effort taxonomy row: a throwaway DB (a proof) has no
    # `db_table_registry`, and its absence must not stop the register existing.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("identity_registry", "identity_registry",
             "the IDENTITY system: who is present (session + worker) running a workflow"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "identity_registry"}


def identity_key_for(session_id: str, worker_key: str, workflow_id: int) -> str:
    """The natural key: session + worker + workflow, joined.

    DERIVED, never accepted from a caller. A caller-supplied key is a value a
    caller can lie about, and two different identities could then share a key.
    """
    return "%s|%s|%s" % (str(session_id).strip(), str(worker_key).strip(),
                         int(workflow_id))


def worker_key_for(session_id: str, channel: str, identity_key: str) -> str:
    """REMOVED 2026-09-24 — this was a CHEAT and is DELETED.

    THE USER (verbatim):

        "worker ID is fake!!! DB driven by id only (auto increase)"
        "this is cheat!!!"
        "old and wrong mis-understand can del!!!!!!!!!!"

    WHAT IT DID: it returned `"W-%s" % sha256(session|channel|identity)[:12]` — a
    key that is COMPUTED, not STORED. No row had it, so it was not an id at all.

    WHAT REPLACED IT: `identity_registry.identity_id`, which IS an
    `INTEGER PRIMARY KEY AUTOINCREMENT`. The worker id is the ROW'S OWN id.

    This function is kept ONLY as a REFUSAL, so a caller that still reaches for
    it is told what to use instead rather than silently getting a fake id.
    """
    raise RuntimeError(
        "worker_key_for is DELETED: a computed key is not a DB id. Use "
        "`identity_registry.identity_id` (INTEGER PRIMARY KEY AUTOINCREMENT) — "
        "the worker id is the row's own id.")


def open_identity(conn: sqlite3.Connection, *, session_id: str,
                  worker_key: str, workflow_id: int, channel: str = "NA",
                  step_no: int = 0, why: str = "",
                  cite_ref: str = "") -> dict[str, Any]:
    """Open an identity for (session, worker, workflow). `chat_id` stays NULL.

    Idempotent on the natural key: an existing identity is RETURNED, so a retry
    does not create a second row for the same who/what.

    THE `why` (purpose) IS REQUIRED (2026-09-24). The user: "chatting is for
    purpose -> we need to provide services, which services can match the user".
    A chat with no purpose cannot be routed to a service, so `why='NA'` is the
    DEFECT, not a default. Measured: the one live row had `why='NA'` — the
    purpose slot existed and was empty. A blank purpose is now REFUSED, naming
    the slot, because a purpose that defaults to NA silently makes every chat
    unroutable while looking valid.
    """
    import worker_registry as wr

    reasons: list[str] = []
    sid = str(session_id or "").strip()
    if not sid:
        reasons.append("session_id is required (who, part 1)")
    wkey = str(worker_key or "").strip()
    if not wkey:
        reasons.append("worker_key is required (who, part 2)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no finding)")
    # THE PURPOSE. `why` is the `why` DIMENSION of `worker_identity`
    # (`dimension_binding_registry`: "why this identity is being established —
    # the work it is opened for"). `NA` is the sentinel for NOT ANSWERED, so it
    # is refused here rather than stored as if it were an answer.
    purpose = str(why or "").strip()
    if not purpose or purpose.upper() == "NA":
        reasons.append(
            "why (purpose) is required: a chat with no purpose cannot be routed "
            "to a service, and 'NA' means NOT ANSWERED — it is not an answer")
    if reasons:
        raise IdentityRefused(reasons)

    ensure_schema(conn)
    wr.ensure_schema(conn)

    # A phantom worker resolves to nothing, so it is refused rather than stored.
    w = conn.execute("SELECT worker_id FROM worker_registry WHERE worker_key = ?",
                     (wkey,)).fetchone()
    if not w:
        raise IdentityRefused(
            ["worker_key %r is not a registered worker — register it first "
             "(a phantom worker resolves to nothing)" % wkey])
    wid = int(w["worker_id"])

    wf = conn.execute("SELECT workflow_id FROM workflow_registry WHERE "
                      "workflow_id = ?", (int(workflow_id),)).fetchone()
    if not wf:
        raise IdentityRefused(
            ["workflow_id %r does not exist — `what` must be a real workflow"
             % workflow_id])

    key = identity_key_for(sid, wkey, int(workflow_id))
    existing = conn.execute(
        "SELECT * FROM identity_registry WHERE identity_key = ?", (key,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "identity": dict(existing)}

    cur = conn.execute(
        "INSERT INTO identity_registry "
        "(identity_key, session_id, worker_id, workflow_id, channel, step_no, "
        " why, cite_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (key, sid, wid, int(workflow_id), str(channel or "NA"), int(step_no),
         str(why or "NA"), str(cite_ref).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM identity_registry WHERE identity_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "identity": dict(row)}


def set_chat_id(conn: sqlite3.Connection, identity_key: str, chat_id: int,
                *, cite_ref: str = "") -> dict[str, Any]:
    """Record the `chat_id` the WORKFLOW produced.

    This is the ONLY writer of `chat_id`, and it is a separate act from opening
    the identity — because the chat does not exist until the workflow runs. A
    caller that supplies a chat_id at open time is refused (see `open_identity`).
    """
    if not str(cite_ref or "").strip():
        raise IdentityRefused(
            ["cite_ref is required: a chat_id must name what produced it"])
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM identity_registry WHERE identity_key = ?",
                       (str(identity_key or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": "no identity with key %r" % identity_key}
    conn.execute(
        "UPDATE identity_registry SET chat_id = ?, updated_at = datetime('now') "
        "WHERE identity_key = ?", (int(chat_id), str(identity_key).strip()))
    conn.commit()
    out = conn.execute("SELECT * FROM identity_registry WHERE identity_key = ?",
                       (str(identity_key).strip(),)).fetchone()
    return {"ok": True, "identity": dict(out)}


def task_ref(conn: sqlite3.Connection, label: str) -> dict[str, Any]:
    """A legacy task label -> the AUTO-INCREMENT task id (`dev_task.id`).

    THE USER'S RULING (2026-09-24):
        "the task ID design is wrong at the past, so be id from task table by
         auto increase, not the tracking ID coding now / task ID is the way to
         connect to chat ID and workflow ID"

    So the IDENTITY is `dev_task.id` — a DB-assigned integer — and a tracking
    code (`TEST-00223ea6-04`, `20.8`) is a LABEL that REACHES it. This helper is
    that reach, and it is the same hop `skill_taxonomy_evidence` uses, so one
    label resolves the same way everywhere instead of each caller inventing a join.
    """
    lab = str(label or "").strip()
    if not lab:
        return {"ok": False, "error": "label is required"}
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, task_label FROM dev_task WHERE task_label = ?", (lab,))]
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": "dev_task is absent: %s" % exc}
    if not rows:
        return {"ok": False, "error": "no dev_task row has task_label %r" % lab}
    if len(rows) > 1:
        return {"ok": False,
                "error": "%d dev_task rows share task_label %r — ambiguous"
                         % (len(rows), lab)}
    return {"ok": True, "task_id": int(rows[0]["id"]), "task_label": lab}


def set_task_id(conn: sqlite3.Connection, identity_key: str, task_id: int,
                *, cite_ref: str = "") -> dict[str, Any]:
    """Set the identity's `task_id` — the JOIN to `chat_id` and `workflow_id`.

    Separate from `open_identity` for the same reason `set_chat_id` is: the
    identity is opened so the WORK can start, and the task does not exist until
    the work names it. A task id supplied at open time would be a fabricated id.
    """
    if not str(cite_ref or "").strip():
        raise IdentityRefused(
            ["cite_ref is required: a task_id must name what produced it"])
    ensure_schema(conn)
    key = str(identity_key or "").strip()
    row = conn.execute("SELECT * FROM identity_registry WHERE identity_key = ?",
                       (key,)).fetchone()
    if not row:
        return {"ok": False, "error": "no identity with key %r" % identity_key}
    conn.execute(
        "UPDATE identity_registry SET task_id = ?, updated_at = datetime('now') "
        "WHERE identity_key = ?", (int(task_id), key))
    conn.commit()
    out = conn.execute("SELECT * FROM identity_registry WHERE identity_key = ?",
                       (key,)).fetchone()
    return {"ok": True, "identity": dict(out)}


def get_identity(conn: sqlite3.Connection, identity_key: str) -> dict[str, Any]:
    """One identity by key. Returns `ok: False` when absent (never raises)."""
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM identity_registry WHERE identity_key = ?",
                       (str(identity_key or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": "no identity with key %r" % identity_key}
    return {"ok": True, "identity": dict(row)}


def list_identities(conn: sqlite3.Connection, *,
                    session_id: str = "") -> dict[str, Any]:
    """All active identities, newest first. Optionally for one session.

    EACH ROW CARRIES A `display_name` AND A `liveness` (added 2026-09-24). The
    user:

        "identity and session.... thare are for name? ui has display name so user
         friendly"
        "active =? status ? he is ON / OFF? can give task to him now, yes or not"

    MEASURED: `identity_key` is `session_id|worker_key|workflow_id` -- a MACHINE
    key, not a name. A human-readable name exists in `chat_center_message.title`
    (e.g. 'Optical-mouse drift is answerable native'), so `display_name` is READ
    from there and falls back to a SHORT session form.

    MEASURED: `is_active` is 1 for ALL 53 rows -- it is a SOFT-DELETE flag, NOT a
    liveness flag. The LIVENESS evidence is `worker_heartbeat`
    (`business_alive`, `heartbeat_at`), and it covers ONLY `worker_id=1`. So
    `liveness` is `ON` / `OFF` / `UNKNOWN`, and a worker with NO heartbeat is
    `UNKNOWN` -- never defaulted to `active`.
    """
    ensure_schema(conn)
    sql = "SELECT * FROM identity_registry WHERE is_active = 1"
    args: list[Any] = []
    if session_id:
        sql += " AND session_id = ?"
        args.append(str(session_id))
    sql += " ORDER BY identity_id DESC"
    rows = [dict(r) for r in conn.execute(sql, args)]

    # The DISPLAY NAME, read from the chat title. A session may have several
    # titles; the NEWEST is used, because it is the most recent thing said.
    names: dict[str, str] = {}
    try:
        for r in conn.execute(
                "SELECT session_id, title FROM chat_center_message "
                "WHERE title IS NOT NULL AND title <> '' "
                "ORDER BY id"):
            names[str(r["session_id"])] = str(r["title"])
    except sqlite3.OperationalError:
        names = {}

    # The LIVENESS, read from `worker_heartbeat`. `business_alive` is the flag and
    # `heartbeat_at` is the time; a worker with NO row is UNKNOWN.
    live: dict[int, dict[str, Any]] = {}
    try:
        for r in conn.execute(
                "SELECT worker_id, business_alive, heartbeat_at, pid "
                "FROM worker_heartbeat ORDER BY heartbeat_at"):
            live[int(r["worker_id"])] = {"business_alive": r["business_alive"],
                                         "heartbeat_at": r["heartbeat_at"],
                                         "pid": r["pid"]}
    except sqlite3.OperationalError:
        live = {}

    # The environment PATH is READ from `working_environment` (ONE table), not
    # assembled from Python dicts. See `_environment_path`.

    # ONE process snapshot for every row, because the counter query costs ~1s
    # and asking it once per row would make a 53-row list cost 53 seconds.
    probes: dict[str, Any] = {}
    try:
        import process_probe as pp
        probe_names = [str(r["process_name"]) for r in conn.execute(
            "SELECT DISTINCT process_name FROM app WHERE is_active=1 "
            "AND process_name IS NOT NULL AND process_name <> ''")]
        if probe_names:
            probes = pp.probe_all(probe_names).get("probes", {})
    except Exception:
        probes = {}

    for r in rows:
        sid = str(r.get("session_id") or "")
        title = names.get(sid, "")
        # THE ENVIRONMENT AS A 3-PART PATH. The user (2026-09-24):
        #   "in table can be IDE | VS code | chat"
        #   "at display = IDE > VS code > chat"
        # So the environment is a PATH: KIND > PRODUCT > SURFACE. MEASURED:
        # `chat_main.ide` holds the PRODUCT ('VS Code'), `channel` holds the KIND
        # ('vscode'), and the SURFACE is 'chat' (the session is a chat).
        #
        # It is computed BEFORE the name, because the DERIVED name is built from
        # the environment (see `_representative_name`).
        r["environment_path"] = _environment_path(conn,
                                                  str(r.get("channel") or ""))
        # THE NAME, and WHERE IT CAME FROM. The user (2026-09-24):
        #   "name and session be display name for respresentative"
        # MEASURED DEFECT THIS FIXES: `display_name` fell back to
        # `_short_session(sid)` -- a TRUNCATED SESSION ID -- for 52 of 53 rows,
        # so the `name` column and the `session` column showed the SAME fact cut
        # two ways. A truncated id is not a name. The name is now DERIVED from
        # the parts the row actually has, and `name_source` DECLARES which.
        r["display_name"], r["name_source"] = _representative_name(
            title, r["environment_path"], int(r.get("identity_id") or 0))
        # THE SESSION, as its OWN display form -- so the `session` column stops
        # being a second copy of the name. It carries the session in PIECES plus
        # the CHANNEL, which is the part of `A` the name does not show.
        r["session_label"] = _session_label(sid, str(r.get("channel") or ""))
        # THE WORKER ID IS THE ROW'S OWN AUTO-INCREMENT ID. The user
        # (2026-09-24):
        #   "worker ID is fake!!! DB driven by id only (auto increase)"
        #   "this is cheat!!!"
        # MEASURED, and the user is RIGHT: the first version DERIVED a key with
        # `sha256` (`W-<hash>`). That key is COMPUTED, not STORED -- no row has
        # it, so it is a CHEAT. `identity_registry.identity_id` IS an
        # `INTEGER PRIMARY KEY AUTOINCREMENT`, so it is the real worker id.
        #
        # The CALLER-SUPPLIED value is captured FIRST, because `worker_id` is
        # about to be overwritten with the real id.
        r["stored_worker_ref"] = r.get("worker_id")
        r["worker_id"] = int(r.get("identity_id") or 0)
        # THE ROLE. The user's formula: `identity = A + role = worker`.
        # MEASURED: `A` is session + channel (both present); `role` was MISSING
        # until `role_id` was added. An identity with NO role is `UNASSIGNED` --
        # a role is a DECISION and is never defaulted.
        r["role_key"] = "UNASSIGNED"
        try:
            import identity_role as irole
            ro = irole.role_of(conn, int(r.get("identity_id") or 0))
            if ro.get("ok"):
                r["role_key"] = ro.get("role_key") or "UNASSIGNED"
        except Exception as exc:
            r["role_key_why"] = "%s: %s" % (type(exc).__name__, exc)
        hb = live.get(int(r.get("worker_id") or 0))
        if hb is None:
            r["liveness"] = "UNKNOWN"
            r["liveness_why"] = ("no worker_heartbeat row for this worker, so "
                                 "whether it can take a task NOW is NOT measured")
        else:
            alive = str(hb["business_alive"]) in ("1", "True", "true")
            r["liveness"] = "ON" if alive else "OFF"
            r["liveness_why"] = ("worker_heartbeat at %s (pid %s)"
                                 % (hb["heartbeat_at"], hb["pid"]))
        # THE STATUS IS THE ENVIRONMENT STATUS. The user (2026-09-24):
        #   "status still = unknow"
        #   "status -> environment status"
        # MEASURED DEFECT THIS FIXES: `status` read `worker_heartbeat`, which
        # covers ONLY `worker_id=1`, so 52 of 53 rows showed UNKNOWN. The user's
        # correction names what the column SHOULD answer: is the app this worker
        # runs in actually running? A worker IS a session in an environment
        # (`worker = environment + identity + session`), so "can I give it a task
        # NOW" depends on whether its ENVIRONMENT is up.
        #
        # The chain is MEASURED, not typed: channel -> app.app_key ->
        # app.process_name -> process_probe. A channel with NO app row is
        # UNKNOWN, never STOPPED: "no app is declared" and "the app is stopped"
        # are different facts.
        r["status"] = "UNKNOWN"
        r["status_source"] = "environment_status:channel->app->process_probe"
        try:
            import environment_status as es
            st = es.status_for(conn, str(r.get("channel") or ""), probes=probes)
            r["status"] = st.get("status") or "UNKNOWN"
            r["status_why"] = st.get("why", "")
            r["status_app"] = st.get("app_name")
            r["status_process"] = st.get("process_name")
            r["status_count"] = st.get("count")
        except Exception as exc:
            r["status_why"] = "%s: %s" % (type(exc).__name__, exc)
        # THE LLM MODEL. The user (2026-09-24):
        #   "identity = session + LLM model"
        #   "1, if by id, you need to have LLM table = 2 table"
        #   "2, 7B is local"
        # MEASURED DEFECT THIS FIXES: `identity_registry` had NO model column at
        # all, so the formula could not be expressed. The model is now a FK to
        # `llm_model.id`, and its NAME and `local` flag are READ through the join
        # -- never copied here, because two copies of `local` could disagree.
        #
        # WHY NOT `chatSessions`: the user ruled it "totally wrong design".
        # MEASURED and the user is right -- it is VS Code's OWN session log, and
        # `agentSessions.model.cache` is a key NAMED "model" whose 73 entries
        # carry NO model name. A model must come from a REGISTER.
        r["llm_id"] = r.get("llm_id")
        r["llm_name"] = None
        r["llm_model_id"] = None
        r["llm_local"] = None
        r["llm_source"] = "llm_model (a register, not chatSessions)"
        try:
            import identity_llm as il
            lo = il.llm_of(conn, int(r.get("identity_id") or 0))
            r["llm_name"] = lo.get("llm_name")
            r["llm_model_id"] = lo.get("llm_model_id")
            r["llm_local"] = lo.get("llm_local")
            r["llm_why"] = lo.get("why", "")
        except Exception as exc:
            r["llm_why"] = "%s: %s" % (type(exc).__name__, exc)
    return {"ok": True, "identities": rows, "count": len(rows),
            "with_name": sum(1 for r in rows if r["display_name"]),
            "on": sum(1 for r in rows if r["liveness"] == "ON"),
            "off": sum(1 for r in rows if r["liveness"] == "OFF"),
            "unknown": sum(1 for r in rows if r["liveness"] == "UNKNOWN"),
            "by_status": {s: sum(1 for r in rows if r["status"] == s)
                          for s in ("RUNNING", "STOPPED", "UNKNOWN")},
            "by_llm": {k: sum(1 for r in rows if (r["llm_name"] or
                                                  "UNASSIGNED") == k)
                       for k in sorted({(r["llm_name"] or "UNASSIGNED")
                                        for r in rows})}}


def _environment_path(conn: sqlite3.Connection, channel: str) -> dict[str, Any]:
    """The environment as a 3-PART PATH: KIND > PRODUCT > SURFACE.

    THE USER (2026-09-24):
        "in table can be IDE | VS code | chat"
        "at display = IDE > VS code > chat"
        "why? should have a single table for that!!!! by DB driven too"

    MEASURED DEFECT THIS FIXES: the path was assembled from TWO HARDCODED DICTS
    (`ENV_KIND`, `ENV_PRODUCT`) plus one observed column. The same fact had THREE
    homes. It is now READ from `working_environment` -- ONE table, DB-driven.

    A channel with NO row is reported `UNKNOWN`, never defaulted, because a
    default would make an undeclared environment look declared.
    """
    try:
        import working_environment as we
        p = we.path_for(conn, str(channel or ""))
    except Exception as exc:
        return {"kind": "UNKNOWN", "product": "UNKNOWN", "surface": "chat",
                "display": "UNKNOWN > UNKNOWN > chat",
                "why": "%s: %s" % (type(exc).__name__, exc)}
    return {"kind": p.get("kind", "UNKNOWN"),
            "product": p.get("product", "UNKNOWN"),
            "surface": p.get("surface", "chat"),
            "display": p.get("display", "UNKNOWN > UNKNOWN > chat"),
            "short": "%s | %s | %s" % (p.get("kind", "UNKNOWN"),
                                        p.get("product", "UNKNOWN"),
                                        p.get("surface", "chat")),
            "declared": bool(p.get("ok")),
            "why": p.get("reason", "")}


def _short_session(session_id: str) -> str:
    """A SHORT, human-readable form of a session id.

    A UUID is not a name, so the first 8 characters are shown with an ellipsis.
    This is a FALLBACK: a session with a real title uses the title instead.
    """
    text = str(session_id or "").strip()
    if not text:
        return "—"
    return text[:8] + "…" if len(text) > 8 else text


def _representative_name(title: str, env: dict[str, Any],
                         identity_id: int) -> tuple[str, str]:
    """A REPRESENTATIVE name for a worker, and WHERE it came from.

    THE USER (2026-09-24):
        "name and session be display name for respresentative"

    MEASURED DEFECT THIS FIXES: `display_name` fell back to `_short_session(sid)`
    -- a TRUNCATED SESSION ID -- for 52 of 53 rows. The `name` column and the
    `session` column therefore showed the SAME fact cut two ways, and neither was
    a name. MEASURED: no register carries a name for those 52 sessions
    (`chat_center_message.title` covers 1, `dev_task.title` covers 0), so a name
    cannot be READ -- it must be DERIVED, and the derivation must DECLARE itself.

    The derived name is built from the parts the row ACTUALLY has, so it is
    representative of the worker rather than of its id:

        <PRODUCT> worker #<id>          e.g. "VS Code worker #52"

    `name_source` is returned alongside, and is one of:

        chat_title  -- a human's own words, READ from `chat_center_message`
        derived     -- built here, because no register carries a name

    A DERIVED name is never presented as if a human had named the worker.
    """
    t = str(title or "").strip()
    if t:
        return t, "chat_title"
    product = str((env or {}).get("product") or "").strip()
    if not product or product == "UNKNOWN":
        product = str((env or {}).get("kind") or "").strip()
    if not product or product == "UNKNOWN":
        product = "Unplaced"
    return "%s worker #%d" % (product, int(identity_id or 0)), "derived"


def _session_label(session_id: str, channel: str) -> str:
    """The SESSION as its OWN display form: the id in PIECES, plus the CHANNEL.

    THE USER (2026-09-24):
        "session ID, in piecs not in full"
        "name and session be display name for respresentative"

    WHY THE CHANNEL IS HERE: the `name` column now carries the worker's name, so
    the `session` column must carry what the name does NOT -- the session id in
    pieces AND the channel, which is the other half of `A` in
    `identity = A + role = worker`. Without the channel the two columns would
    still be two views of one fact.
    """
    text = str(session_id or "").strip()
    ch = str(channel or "").strip()
    if not text:
        return "—" if not ch else "— @ %s" % ch
    parts = text.split("-")
    if len(parts) < 2:
        piece = text
    else:
        piece = "%s-…-%s" % (parts[0], parts[-1])
    return "%s @ %s" % (piece, ch) if ch else piece


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", default="")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--session", default="")
    ap.add_argument("--worker", default="")
    ap.add_argument("--workflow", type=int, default=0)
    ap.add_argument("--channel", default="NA")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.open:
            print(json.dumps(open_identity(
                conn, session_id=args.session, worker_key=args.worker,
                workflow_id=args.workflow, channel=args.channel,
                cite_ref="identity_registry.py:CLI"), ensure_ascii=False,
                indent=2))
            return 0
        if args.get:
            print(json.dumps(get_identity(conn, args.get), ensure_ascii=False,
                             indent=2))
            return 0
        if args.list:
            print(json.dumps(list_identities(conn), ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
