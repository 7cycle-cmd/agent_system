"""identity_llm.py -- the LLM link, so `identity = session + LLM model` holds.

THE USER'S FORMULA (2026-09-24, verbatim)
-----------------------------------------
    "identity = session + LLM model"

THE USER'S DECISION: TWO TABLES, JOINED BY ID
---------------------------------------------
    "1, if by id, you need to have LLM table = 2 table"

MEASURED -- the LLM table ALREADY EXISTS and is complete:

    llm_model: id | name | model_id | text | visual | local | is_active
      id=1  Qwen2.5 7B               qwen2.5:7b-instruct              local=1
      id=2  Qwen2.5 7B-vl            qwen2.5vl:7b                     local=1
      id=3  Qwen 3.8 27B             qwen/qwen3.8-27b                 local=0
      id=4  DeepSeek V4.1 Flash 0731 deepseek/deepseek-v4-flash-0731  local=0

So the design is TWO tables, and the ONLY missing link is
`identity_registry.llm_id` -- a FK to `llm_model.id`.

THE USER'S DECISION: `Local` IS DERIVED, NOT STORED TWICE
---------------------------------------------------------
    "2, 7B is local"

MEASURED: `llm_model.local` ALREADY holds that fact (`Qwen2.5 7B` -> `local=1`).
So `Local` is NOT a column on `identity_registry` -- it is READ through the join.
Storing it twice would let the two copies disagree, which is the defect class
this repo has recorded repeatedly.

WHY NOT `chatSessions`
----------------------
The user: "chatsession is totally wrong design, will remove".

MEASURED, and the user is right: `chatSessions/*.jsonl` is VS Code's OWN session
log (76 files), and `agentSessions.model.cache` is a key NAMED "model" whose 73
entries carry NO model name (all `providerType='local'`). A model must come from
a REGISTER, not from another program's log file. This module never reads it.

THE FORMULA REFUSES A MISSING PART
----------------------------------
`formula()` returns `ok: False` with the missing part named, rather than
defaulting it. A defaulted model would make an unassigned identity look assigned.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "agent.db"

# THE FORMULA, stated once so a reader and a proof cite the same string.
FORMULA = "identity = session + LLM model"
PARTS = ("session", "llm")
SESSION_PARTS = ("session_id",)


class LlmRefused(Exception):
    """Raised when a write would record a model the register does not know."""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    MEASURED DEFECT (2026-09-24, in `computer_presence`): a reader that depends
    on the CALLER having set the row factory breaks the moment a new caller
    forgets. The reader sets it itself.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add `llm_id` to `identity_registry` if it is absent.

    A MIGRATION, not a re-create: the table already holds 53 rows, so the column
    is ADDED. `ALTER TABLE ... ADD COLUMN` is the only safe form for an existing
    table, and it is idempotent when guarded by a column check.
    """
    _as_rows(conn)
    cols = [str(r["name"]) for r in conn.execute(
        "PRAGMA table_info(identity_registry)")]
    if "llm_id" in cols:
        return {"ok": True, "action": "already_present", "column": "llm_id"}
    conn.execute("ALTER TABLE identity_registry ADD COLUMN llm_id INTEGER")
    conn.commit()
    return {"ok": True, "action": "added", "column": "llm_id"}


def models(conn: sqlite3.Connection) -> dict[str, Any]:
    """The LLM VOCABULARY, READ from `llm_model`. Never typed here.

    `local` is carried because the user's `Local` column is DERIVED from it.
    """
    _as_rows(conn)
    rows = conn.execute(
        "SELECT id, name, model_id, text, visual, local, description "
        "FROM llm_model WHERE is_active=1 ORDER BY id").fetchall()
    return {"ok": True, "models": [dict(r) for r in rows],
            "ids": [int(r["id"]) for r in rows], "count": len(rows)}


def _model_row(conn: sqlite3.Connection, llm_id: int) -> dict[str, Any] | None:
    _as_rows(conn)
    row = conn.execute(
        "SELECT id, name, model_id, local FROM llm_model "
        "WHERE id=? AND is_active=1", (int(llm_id),)).fetchone()
    return dict(row) if row else None


def resolve_alias(conn: sqlite3.Connection, alias: str) -> dict[str, Any]:
    """`{ok, llm_id, name, model_id}` for a provider's own model id.

    WHY THIS EXISTS (the human, 2026-09-25):
        "get by my LLM table under identity table? / or you are fucking for
         handcode again"

    MEASURED, and the human is right: the model must come from the REGISTER
    (`llm_model`), linked by `identity_registry.llm_id`. My previous change wrote
    a free-text name read from `chatSessions/*.jsonl` — a design the human had
    ALREADY REJECTED, recorded verbatim in this module's own docstring:

        "chatsession is totally wrong design, will remove"
        "A model must come from a REGISTER, not from another program's log file."

    IT IS AN EXACT MATCH ON A DECLARED ALIAS, NEVER A FUZZY MATCH. MEASURED, the
    provider's id and the registered id DIFFER:

        VS Code id   : deepseek/deepseek-v4.1-flash
        llm_model id : deepseek/deepseek-v4-flash-0731

    A substring match would join them BY LUCK, and would join the WRONG model the
    moment a second DeepSeek variant is registered. An alias is a DECLARED fact;
    a fuzzy match is a guess. So an unknown alias is REFUSED, and the refusal
    names what IS registered.
    """
    _as_rows(conn)
    a = str(alias or "").strip()
    if not a:
        return {"ok": False, "code": "EMPTY_ALIAS", "alias": a}
    try:
        row = conn.execute(
            "SELECT a.llm_id, m.name, m.model_id, m.local "
            "FROM llm_model_alias a JOIN llm_model m ON m.id = a.llm_id "
            "WHERE a.alias=? AND a.is_active=1 AND m.is_active=1", (a,)).fetchone()
    except Exception as exc:
        return {"ok": False, "code": "ALIAS_TABLE_UNREADABLE", "alias": a,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    if row is None:
        known = [r[0] for r in conn.execute(
            "SELECT alias FROM llm_model_alias WHERE is_active=1 ORDER BY alias")]
        return {"ok": False, "code": "UNKNOWN_ALIAS", "alias": a,
                "known": known,
                "message": ("alias %r is not registered; an unknown alias is "
                            "REFUSED, never fuzzy-matched (known: %s)"
                            % (a, known))}
    return {"ok": True, "alias": a, "llm_id": int(row["llm_id"]),
            "name": row["name"], "model_id": row["model_id"],
            "local": row["local"]}


def assign_for_session_by_alias(conn: sqlite3.Connection, *, session_id: str,
                                alias: str, cite_ref: str,
                                role_key: str | None = None,
                                commit: bool = True) -> dict[str, Any]:
    """Resolve a provider's model id to a REGISTERED model, then assign it.

    The two steps are separate on purpose: `resolve_alias` REFUSES an unknown
    alias, and `assign_for_session` REFUSES a session with no active identity.
    Neither is softened here, so a caller cannot get a half-done assignment.
    """
    r = resolve_alias(conn, alias)
    if not r.get("ok"):
        return r
    out = assign_for_session(conn, session_id=session_id, llm_id=r["llm_id"],
                             cite_ref=cite_ref, role_key=role_key, commit=commit)
    out["alias"] = r["alias"]
    out["llm_name"] = r["name"]
    return out


def assign_llm(conn: sqlite3.Connection, *, identity_id: int, llm_id: int,
               cite_ref: str, role_key: str | None = None,
               commit: bool = True) -> dict[str, Any]:
    """Assign ONE model (and optionally ONE role) to ONE identity.

    REFUSES an unknown model, an unknown identity, or an unknown role.

    A REFUSAL is raised, not returned as a soft failure: a caller that ignores a
    soft failure would leave the row unassigned while believing it succeeded.

    THE USER (2026-09-25, verbatim):
        "-> identity / worker = LLM : 豆包 , local = 0"
        "-> identity / worker = LLM : DeepSeek, local = 0"
        "-> identity / worker = LLM : Gemini, local = 0"

    MEASURED: `identity_registry` carries BOTH `llm_id` AND `role_id`, and all
    53 rows have both NULL. So the user's "identity = LLM" AND the role the
    step-1 page reports are the SAME row's two columns -- this writer sets
    both, so the two facts can never be written by different code paths.
    """
    _as_rows(conn)
    ident = conn.execute(
        "SELECT identity_id FROM identity_registry WHERE identity_id=?",
        (int(identity_id),)).fetchone()
    if ident is None:
        raise LlmRefused("identity_id %s does not exist" % identity_id)
    m = _model_row(conn, llm_id)
    if m is None:
        known = models(conn)["ids"]
        raise LlmRefused(
            "llm_id %s is not in `llm_model` (known ids: %s)" % (llm_id, known))
    rk = None
    if role_key is not None:
        rk = str(role_key).strip() or None
        if rk is not None:
            r = conn.execute(
                "SELECT role_key FROM role_registry WHERE role_key=? AND "
                "is_active=1", (rk,)).fetchone()
            if r is None:
                known_r = [x["role_key"] for x in conn.execute(
                    "SELECT role_key FROM role_registry WHERE is_active=1")]
                raise LlmRefused("role_key %r is not in `role_registry` "
                                 "(known: %s)" % (rk, known_r))
    if rk is None:
        conn.execute(
            "UPDATE identity_registry SET llm_id=?, cite_ref=?, "
            "updated_at=datetime('now') WHERE identity_id=?",
            (int(llm_id), str(cite_ref or ""), int(identity_id)))
    else:
        conn.execute(
            "UPDATE identity_registry SET llm_id=?, "
            "role_id=(SELECT rowid FROM role_registry WHERE role_key=? AND "
            "is_active=1), cite_ref=?, updated_at=datetime('now') "
            "WHERE identity_id=?",
            (int(llm_id), rk, str(cite_ref or ""), int(identity_id)))
    if commit:
        conn.commit()
    return {"ok": True, "identity_id": int(identity_id), "llm_id": int(llm_id),
            "llm_name": m["name"], "llm_model_id": m["model_id"],
            "local": int(m["local"] or 0), "role_key": rk}


def assign_for_session(conn: sqlite3.Connection, *, session_id: str,
                       llm_id: int, cite_ref: str,
                       role_key: str | None = None,
                       commit: bool = True) -> dict[str, Any]:
    """Assign the model to a SESSION's active identity.

    A caller usually has the SESSION, not the row id, so this resolves the
    identity first and REFUSES a session with no active identity rather than
    silently doing nothing.
    """
    _as_rows(conn)
    sid = str(session_id or "").strip()
    if not sid:
        raise LlmRefused("session_id is required")
    row = conn.execute(
        "SELECT identity_id FROM identity_registry WHERE session_id=? AND "
        "is_active=1 ORDER BY identity_id DESC LIMIT 1", (sid,)).fetchone()
    if row is None:
        raise LlmRefused("session_id %r has no active identity in "
                         "`identity_registry`" % sid)
    out = assign_llm(conn, identity_id=int(row["identity_id"]), llm_id=llm_id,
                     cite_ref=cite_ref, role_key=role_key, commit=commit)
    out["session_id"] = sid
    return out


def llm_for_session(conn: sqlite3.Connection,
                    session_id: str) -> dict[str, Any]:
    """The model and role a SESSION's identities run on. Never raises."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT i.identity_id, i.session_id, i.channel, i.llm_id, "
            "i.role_id, m.name AS llm_name, m.model_id, m.local, "
            "r.role_key FROM identity_registry i "
            "LEFT JOIN llm_model m ON m.id = i.llm_id "
            "LEFT JOIN role_registry r ON r.rowid = i.role_id "
            "WHERE i.session_id=? AND i.is_active=1 "
            "ORDER BY i.identity_id", (str(session_id),))]
    except Exception as exc:
        return {"ok": False, "rows": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "rows": rows, "count": len(rows)}


def llm_of(conn: sqlite3.Connection, identity_id: int) -> dict[str, Any]:
    """The model of ONE identity, READ through the join. Never defaults."""
    _as_rows(conn)
    row = conn.execute(
        "SELECT i.llm_id, m.name AS llm_name, m.model_id AS llm_model_id, "
        "m.local AS llm_local FROM identity_registry i "
        "LEFT JOIN llm_model m ON m.id = i.llm_id "
        "WHERE i.identity_id=?", (int(identity_id),)).fetchone()
    if row is None:
        return {"ok": False, "identity_id": int(identity_id),
                "llm_id": None, "llm_name": None, "llm_local": None,
                "why": "identity_id %s does not exist" % identity_id}
    if row["llm_id"] is None:
        return {"ok": True, "identity_id": int(identity_id), "llm_id": None,
                "llm_name": None, "llm_model_id": None, "llm_local": None,
                "why": "no llm_id on this identity: the model is a DECISION and "
                       "is REPORTED, not defaulted"}
    return {"ok": True, "identity_id": int(identity_id),
            "llm_id": int(row["llm_id"]), "llm_name": row["llm_name"],
            "llm_model_id": row["llm_model_id"],
            "llm_local": int(row["llm_local"] or 0),
            "why": "model `%s` (local=%s)" % (row["llm_name"],
                                              row["llm_local"])}


def formula(conn: sqlite3.Connection, identity_id: int) -> dict[str, Any]:
    """Evaluate `identity = session + LLM model` for ONE identity.

    REFUSES a missing part rather than defaulting it: a defaulted model would
    make an unassigned identity look assigned.
    """
    _as_rows(conn)
    row = conn.execute(
        "SELECT identity_id, session_id, llm_id FROM identity_registry "
        "WHERE identity_id=?", (int(identity_id),)).fetchone()
    if row is None:
        return {"ok": False, "identity_id": int(identity_id),
                "reasons": ["identity_id %s does not exist" % identity_id],
                "formula": FORMULA}
    sid = str(row["session_id"] or "").strip()
    llm = llm_of(conn, int(identity_id))
    reasons: list[str] = []
    if not sid:
        reasons.append("session_id is required (identity = session + LLM)")
    if row["llm_id"] is None:
        reasons.append("llm_id is required (identity = session + LLM)")
    if reasons:
        return {"ok": False, "identity_id": int(identity_id),
                "session_id": sid, "llm_id": row["llm_id"],
                "reasons": reasons, "formula": FORMULA,
                "why": "the formula is INCOMPLETE, so this is not yet an identity"}
    return {"ok": True, "identity_id": int(identity_id), "session_id": sid,
            "llm_id": int(row["llm_id"]), "llm_name": llm.get("llm_name"),
            "llm_local": llm.get("llm_local"),
            "display": "session(%s) + llm(%s, local=%s)"
                       % (sid[:8] + "…" if len(sid) > 8 else sid,
                          llm.get("llm_name"), llm.get("llm_local")),
            "formula": FORMULA}


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every identity, grouped by its model. REFUSES an EMPTY register."""
    _as_rows(conn)
    rows = conn.execute(
        "SELECT i.identity_id, i.session_id, i.llm_id, m.name AS llm_name, "
        "m.local AS llm_local FROM identity_registry i "
        "LEFT JOIN llm_model m ON m.id = i.llm_id "
        "WHERE i.is_active=1 ORDER BY i.identity_id").fetchall()
    if not rows:
        return {"ok": False, "reason": "identity_registry is EMPTY, so the LLM "
                                       "link cannot be audited"}
    by: dict[str, int] = {}
    for r in rows:
        k = str(r["llm_name"]) if r["llm_id"] is not None else "UNASSIGNED"
        by[k] = by.get(k, 0) + 1
    assigned = sum(1 for r in rows if r["llm_id"] is not None)
    return {"ok": True, "rows": len(rows), "by_llm": by, "assigned": assigned,
            "unassigned": len(rows) - assigned,
            "vocabulary": [m["name"] for m in models(conn)["models"]],
            "formula": FORMULA,
            "why": "an identity with NO model is session ONLY; the model is a "
                   "DECISION and is REPORTED, not defaulted"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--formula", type=int, default=0)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
            return 0
        if args.models:
            print(json.dumps(models(conn), indent=2, ensure_ascii=False))
            return 0
        if args.formula:
            print(json.dumps(formula(conn, args.formula), indent=2,
                             ensure_ascii=False))
            return 0
        print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
