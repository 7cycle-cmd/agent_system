"""pick_flow.py -- the THREE-STEP pick: environment -> LLM -> tool.

THE USER'S DESIGN (2026-09-24, verbatim)
----------------------------------------
    "+ UI -> http://127.0.0.1:18765/llm-tasks/evidence/step2_worker"
    "show all the LLM for user to select"
    "when step 1 = vscode, step 2 LLM for deepseekSeek V4.1 / Mooon Shot AI :
     Kimi K2.7 Code / Qwen3.8 27b with bg-color : blue, other bg-color without,
     same for other"
    "is tips for user,"
    "onclick LLM = submit -> step 3"
    "+ UI -> http://127.0.0.1:18765/llm-tasks/evidence/step3_tools"
    "show chat center / Task Center / QC Center (new) for user to select"
    "-> chat center X research"
    "-> task center X writing"
    "-> QC Center X Verfitier"
    "when step 2 = deepseekSeek V4.1, step 3 task center with bg-color : blue,
     other bg-color without, same for other"
    "is tips for user,"

THE TIP IS DERIVED, NEVER TYPED
-------------------------------
The blue highlight is a TIP, and a tip that is typed into the UI would be a
second copy of a fact the registers already hold. So:

    step 2 tip  = the models SUITED to the step-1 environment
    step 3 tip  = the tool whose ROLE matches the step-2 model

MEASURED: `llm_model` carries `text` / `visual` / `local`, and `tool_center`
carries `role_key`. So both tips are READ, not invented.

THE MODEL -> ROLE RULE, AND WHY IT IS DECLARED
----------------------------------------------
The user's example: "when step 2 = deepseekSeek V4.1, step 3 task center with
bg-color : blue". So a model is suited to a role. MEASURED: `llm_model` has no
role column, so the rule is DECLARED here as a table, and it is stated ONCE so a
seed and a proof cite the same list.

    a model that can WRITE (text=1, not local)  -> writer  -> task center
    a model that can SEE   (visual=1)           -> verifier -> QC Center
    a local model                               -> researcher -> chat center

A model with NO rule is NOT tipped, and the flow REPORTS that rather than
guessing.

NEVER RAISES ON A READ
----------------------
`flow()` and `tips()` return `ok: False` with a `why` rather than raising.
`pick()` DOES raise, because a caller that ignores a soft failure would leave the
flow unchanged while believing it succeeded.
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

SOURCE = "pick_flow:working_environment -> llm_model -> tool_center"

# THE MODEL -> ROLE RULE, declared ONCE. Each entry is
# (model_id_substring, role_key, why). The FIRST match wins, so the order is the
# priority and a reader can see it.
MODEL_ROLE_RULES: tuple[tuple[str, str, str], ...] = (
    ("deepseek", "writer",
     "a remote text model PRODUCES the artifact, so it serves the writer role"),
    ("kimi", "writer",
     "a remote code model PRODUCES the artifact, so it serves the writer role"),
    ("qwen3.8", "writer",
     "a remote text model PRODUCES the artifact, so it serves the writer role"),
    ("qwen2.5vl", "verifier",
     "a VISION model CHECKS what is on screen, so it serves the verifier role"),
    ("qwen2.5", "researcher",
     "a LOCAL text model READS and REPORTS, so it serves the researcher role"),
)


class PickRefused(Exception):
    """Raised when a pick would name a row the registers do not know."""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent."""
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `pick_flow` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.PICK_FLOW_DDL)
    conn.commit()
    return {"ok": True, "table": "pick_flow"}


def role_for_model(model_id: str) -> dict[str, Any]:
    """The role a model is suited to, from the DECLARED rule. Never guesses."""
    mid = str(model_id or "").strip().lower()
    if not mid:
        return {"ok": False, "model_id": "", "role_key": None,
                "why": "model_id is required"}
    for needle, role_key, why in MODEL_ROLE_RULES:
        if needle in mid:
            return {"ok": True, "model_id": mid, "role_key": role_key,
                    "why": why, "matched": needle}
    return {"ok": True, "model_id": mid, "role_key": None,
            "why": "no rule maps `%s` to a role, so it is NOT tipped" % mid}


def tips(conn: sqlite3.Connection, *, environment_id: int | None = None,
         llm_id: int | None = None) -> dict[str, Any]:
    """The TIPS for each step. DERIVED from the registers. Never raises."""
    _as_rows(conn)
    out: dict[str, Any] = {"ok": True, "source": SOURCE}

    # STEP 2 TIP: the models suited to the step-1 environment. MEASURED: an
    # environment's `kind` decides which models are usable -- an IDE needs a
    # TEXT model, a screen environment needs a VISION model.
    step2_tip: list[dict[str, Any]] = []
    env_kind = ""
    if environment_id is not None:
        try:
            row = conn.execute(
                "SELECT kind, product, display FROM working_environment "
                "WHERE environment_id=? AND is_active=1",
                (int(environment_id),)).fetchone()
            if row is not None:
                env_kind = str(row["kind"] or "")
                out["environment"] = {"environment_id": int(environment_id),
                                      "kind": env_kind,
                                      "display": row["display"]}
        except Exception as exc:
            out["environment_error"] = "%s: %s" % (type(exc).__name__, exc)
    try:
        models = [dict(r) for r in conn.execute(
            "SELECT id, name, model_id, text, visual, local FROM llm_model "
            "WHERE is_active=1 ORDER BY id")]
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    for m in models:
        # A model is TIPPED when it can do the job the environment needs.
        if env_kind.upper() == "IDE":
            tipped = int(m["text"] or 0) == 1
            why = "an IDE environment needs a TEXT model"
        elif env_kind.upper() in ("UI", "PC"):
            tipped = int(m["visual"] or 0) == 1 or int(m["text"] or 0) == 1
            why = "a screen environment can use a TEXT or a VISION model"
        else:
            tipped = int(m["text"] or 0) == 1
            why = "a non-IDE environment defaults to a TEXT model"
        rr = role_for_model(str(m["model_id"]))
        step2_tip.append({
            "llm_id": int(m["id"]), "name": m["name"],
            "model_id": m["model_id"], "local": int(m["local"] or 0),
            "tipped": bool(tipped), "tip_why": why,
            "role_key": rr.get("role_key"), "role_why": rr.get("why"),
        })
    out["step2"] = {"models": step2_tip,
                    "tipped_count": sum(1 for m in step2_tip if m["tipped"])}

    # STEP 3 TIP: the tool whose ROLE matches the step-2 model.
    step3_tip: list[dict[str, Any]] = []
    picked_role = None
    if llm_id is not None:
        try:
            m = conn.execute(
                "SELECT id, name, model_id FROM llm_model WHERE id=? AND "
                "is_active=1", (int(llm_id),)).fetchone()
            if m is not None:
                rr = role_for_model(str(m["model_id"]))
                picked_role = rr.get("role_key")
                out["llm"] = {"llm_id": int(m["id"]), "name": m["name"],
                              "model_id": m["model_id"],
                              "role_key": picked_role,
                              "role_why": rr.get("why")}
        except Exception as exc:
            out["llm_error"] = "%s: %s" % (type(exc).__name__, exc)
    try:
        tools = [dict(r) for r in conn.execute(
            "SELECT tool_key, name, role_key FROM tool_center "
            "WHERE is_active=1 ORDER BY tool_id")]
    except Exception as exc:
        tools = []
        out["tools_error"] = "%s: %s" % (type(exc).__name__, exc)
    for t in tools:
        tipped = picked_role is not None and str(t["role_key"]) == picked_role
        step3_tip.append({
            "tool_key": t["tool_key"], "name": t["name"],
            "role_key": t["role_key"], "tipped": bool(tipped),
            "tip_why": ("the picked model serves role `%s`, which this tool "
                        "serves" % picked_role) if tipped else
                       ("the picked model serves role `%s`, not `%s`"
                        % (picked_role, t["role_key"])) if picked_role else
                       "no model is picked yet, so no tool is tipped",
        })
    out["step3"] = {"tools": step3_tip,
                    "tipped_count": sum(1 for t in step3_tip if t["tipped"]),
                    "picked_role": picked_role}
    return out


def current(conn: sqlite3.Connection) -> dict[str, Any]:
    """The ONE active flow, or an empty one. Never raises."""
    _as_rows(conn)
    try:
        row = conn.execute(
            "SELECT * FROM pick_flow WHERE is_active=1 ORDER BY flow_id DESC "
            "LIMIT 1").fetchone()
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    if row is None:
        return {"ok": True, "flow": None,
                "why": "no pick has been made yet, so the flow is EMPTY"}
    return {"ok": True, "flow": dict(row)}


def pick(conn: sqlite3.Connection, *, step: int, value: Any,
         cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Set ONE step of the flow. REFUSES an unknown row.

    A REFUSAL is raised, not returned as a soft failure: a caller that ignores a
    soft failure would leave the flow unchanged while believing it succeeded.

    Setting an EARLIER step CLEARS the later ones, because a later pick was made
    against the earlier one -- keeping it would leave a tool chosen for a model
    that is no longer picked.
    """
    _as_rows(conn)
    s = int(step)
    if s not in (1, 2, 3):
        raise PickRefused("step must be 1, 2 or 3, got %r" % (step,))
    row = conn.execute(
        "SELECT * FROM pick_flow WHERE is_active=1 ORDER BY flow_id DESC "
        "LIMIT 1").fetchone()
    cur = dict(row) if row else {"environment_id": None, "llm_id": None,
                                 "tool_key": None, "role_key": None}

    if s == 1:
        try:
            eid = int(value)
        except Exception:
            raise PickRefused("step 1 needs an integer environment_id, got %r"
                              % (value,))
        env = conn.execute(
            "SELECT environment_id FROM working_environment WHERE "
            "environment_id=? AND is_active=1", (eid,)).fetchone()
        if env is None:
            raise PickRefused("environment_id %s is not in "
                              "`working_environment`" % eid)
        cur.update({"environment_id": eid, "llm_id": None, "tool_key": None,
                    "role_key": None})
    elif s == 2:
        try:
            lid = int(value)
        except Exception:
            raise PickRefused("step 2 needs an integer llm_id, got %r" % (value,))
        m = conn.execute("SELECT id, model_id FROM llm_model WHERE id=? AND "
                         "is_active=1", (lid,)).fetchone()
        if m is None:
            raise PickRefused("llm_id %s is not in `llm_model`" % lid)
        rr = role_for_model(str(m["model_id"]))
        cur.update({"llm_id": lid, "tool_key": None,
                    "role_key": rr.get("role_key")})
    else:
        tk = str(value or "").strip()
        t = conn.execute("SELECT tool_key, role_key FROM tool_center WHERE "
                         "tool_key=? AND is_active=1", (tk,)).fetchone()
        if t is None:
            raise PickRefused("tool_key `%s` is not in `tool_center`" % tk)
        cur.update({"tool_key": tk, "role_key": str(t["role_key"])})

    # SUPERSEDE, never overwrite: the history stays readable.
    conn.execute("UPDATE pick_flow SET is_active=0, "
                 "updated_at=datetime('now') WHERE is_active=1")
    conn.execute(
        "INSERT INTO pick_flow (slot, environment_id, llm_id, tool_key, "
        "role_key, cite_ref) VALUES (1, ?, ?, ?, ?, ?)",
        (cur.get("environment_id"), cur.get("llm_id"), cur.get("tool_key"),
         cur.get("role_key"), str(cite_ref or "")))
    if commit:
        conn.commit()
    return {"ok": True, "step": s, "flow": cur}


def flow(conn: sqlite3.Connection) -> dict[str, Any]:
    """The whole flow: the current pick, plus the TIPS for each step."""
    cur = current(conn)
    if not cur.get("ok"):
        return cur
    f = cur.get("flow") or {}
    t = tips(conn, environment_id=f.get("environment_id"),
             llm_id=f.get("llm_id"))
    return {"ok": True, "flow": f, "tips": t, "source": SOURCE,
            "steps": [
                {"step": 1, "key": "environment_id",
                 "path": "/llm-tasks/evidence/step1_environment",
                 "picked": f.get("environment_id")},
                {"step": 2, "key": "llm_id",
                 "path": "/llm-tasks/evidence/step2_worker",
                 "picked": f.get("llm_id")},
                {"step": 3, "key": "tool_key",
                 "path": "/llm-tasks/evidence/step3_tools",
                 "picked": f.get("tool_key")},
            ]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--flow", action="store_true")
    ap.add_argument("--tips", action="store_true")
    ap.add_argument("--env", type=int, default=0)
    ap.add_argument("--llm", type=int, default=0)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
            return 0
        if args.tips:
            print(json.dumps(tips(conn, environment_id=args.env or None,
                                  llm_id=args.llm or None),
                             indent=2, ensure_ascii=False))
            return 0
        print(json.dumps(flow(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
