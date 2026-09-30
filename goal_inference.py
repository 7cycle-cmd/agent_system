# -*- coding: utf-8 -*-
"""goal_inference.py — deriving the GOAL a chat yields. A CAPABILITY.

THE USER (2026-09-24)
---------------------
    "Next is purpose, let's reanme that to `goal_inference` + `goal_resolver`
     under which module ?they are capability?"
    "what are they doing ? chat module help to have final key factor (got the
     purpose) -> match the purpose by `goal_inference` + `goal_resolver`"

IS IT A CAPABILITY? YES — and here is the evidence, not an opinion
-----------------------------------------------------------------
`capability_kind_registry` declares FIVE kinds, each with a definition, and a
capability in this repo is a VERB A PROVIDER PERFORMS:

    thinking : "The provider REASONS: it reads text or an image and returns a
                judgement. Served by an LLM."

Inferring a goal from a chat's recorded factors IS "read text and return a
judgement", so it is `thinking` BY THE DECLARED DEFINITION. It is therefore a
capability, owned by `llm_runtime` (module 15115) — the module that already owns
the other two `thinking` capabilities (`llm.text_completion`,
`llm.vision_analyze`).

NOT a module: `capability_registry.module_id` is `NOT NULL` + FK, so a
capability BELONGS TO a module and cannot BE one.

TERM-FIRST
----------
`goal` collides with NO table name and NO `terminology_registry` term
(measured), but the name is still REGISTERED before this module's code uses it —
the repo's rule is "no name without a registered term".

NO FABRICATED JUDGEMENT
-----------------------
`infer()` returns a goal ONLY when a provider actually answered. With no
provider it returns `NO_VERDICT` naming the missing piece, and with an answer it
returns whatever the provider said — it never supplies a default. A goal that
defaulted would make every chat routable while looking valid.

Run:
    .\\.venv\\Scripts\\python.exe goal_inference.py --measure
    .\\.venv\\Scripts\\python.exe goal_inference.py --register-terms
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The capability this module IS. `kind='thinking'` and `module_id` are read at
# registration time from the DB, never hardcoded to a guessed id.
CAPABILITY_KEY = "llm.goal_inference"
CAPABILITY_KIND = "thinking"
OWNING_MODULE_KEY = "llm_runtime"

# THE THREE TERMS, and why three (MEASURED: `purpose` carries TWO meanings today
# — `purpose_route_registry.purpose_key` is the TEXT a route matches, and
# `identity_registry.why` is the per-chat purpose).
TERMS: tuple[dict[str, Any], ...] = (
    {
        "term_key": "goal",
        "term_kind": "entity",
        "definition": (
            "The FINAL KEY FACTOR a chat yields: the work the chat was opened to "
            "discuss, named concretely enough to be MATCHED to a route. Replaces "
            "the word `purpose`, which carried TWO meanings (the text a route "
            "matches, and a per-chat why)."),
        "cite_ref": ("measured: purpose_route_registry.purpose_key and "
                     "identity_registry.why both hold a `purpose` today; a "
                     "collision scan of sqlite_master and terminology_registry "
                     "finds `goal` in NEITHER"),
        # CORRECTED 2026-09-24: `final key factor` was a DESCRIPTION in a NAME
        # slot. An alias must be a NAME the system actually used, so it is gone.
        "alias_list": ["purpose"],
    },
    {
        "term_key": "goal_inference",
        "term_kind": "action",
        "definition": (
            "DERIVING the goal from the factors a chat RECORDS (provider, "
            "source, worker, session, transcript). A CAPABILITY of kind "
            "`thinking` per capability_kind_registry, served by module "
            "`llm_runtime`; it does not MATCH the goal, it produces it."),
        "cite_ref": ("measured: SELECT definition FROM capability_kind_registry "
                     "WHERE kind_key='thinking' -> 'the provider REASONS ... and "
                     "returns a judgement'"),
        "alias_list": [],
    },
    {
        "term_key": "goal_resolver",
        "term_kind": "part",
        "definition": (
            "MATCHING an inferred goal against the route register and returning "
            "service + workflow. A ROUTER step, NOT a capability: no "
            "capability_kind describes a lookup, so registering it as one would "
            "invent a 6th kind."),
        "cite_ref": ("measured: SELECT kind_key, definition FROM "
                     "capability_kind_registry -> 5 rows, each definition names "
                     "a provider VERB (reasons / captures / acts / speaks / "
                     "produces-or-gates source code)"),
        "alias_list": [],
    },
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


# --------------------------------------------------------------------------
# TERM-FIRST: register the names before any code uses them
# --------------------------------------------------------------------------
def collision_check(conn: sqlite3.Connection, term_key: str) -> dict[str, Any]:
    """Is the name ALREADY taken by a table or a registered term?

    RE-RUN, not asserted: the register is live and a third party can add a term
    while this plan executes, so "it was free when I measured" is not evidence.

    A DERIVATION IS NOT A COLLISION. `goal_inference` and `goal_resolver` are
    COMPOUNDS OF `goal` — they contain it because they are built on it. Treating
    a compound as a collision is a FALSE POSITIVE (measured: registering `goal`
    last reported a collision with the two terms derived FROM it). So the check
    separates three cases:

      * EXACT same name            -> a real collision
      * `<key>_...`                -> a DERIVATION of this name (reported)
      * contains `<key>` elsewhere -> a collision (a different family)
    """
    key = str(term_key).strip().lower()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE lower(name) LIKE ?",
        ("%" + key + "%",))]
    derivations: list[str] = []
    collisions: list[str] = []
    if _table_exists(conn, "terminology_registry"):
        for r in conn.execute(
                "SELECT term_key FROM terminology_registry WHERE "
                "lower(term_key) LIKE ?", ("%" + key + "%",)):
            other = str(r[0])
            low = other.lower()
            if low == key:
                # The term's OWN row is not a collision with itself — an
                # idempotent re-run must not refuse.
                continue
            if low.startswith(key + "_"):
                derivations.append(other)
            else:
                collisions.append(other)
    return {"ok": True, "term_key": term_key, "tables": tables,
            "derivations": sorted(derivations), "other_terms": sorted(collisions),
            "free": not tables and not collisions,
            "cite": ("measured: sqlite_master LIKE %%%s%% -> %s; "
                     "terminology_registry LIKE %%%s%% -> derivations %s, "
                     "other %s" % (key, tables, key, sorted(derivations),
                                   sorted(collisions)))}


def register_terms(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register `goal`, `goal_inference`, `goal_resolver`. Idempotent."""
    import terminology_registry as tr
    tr.ensure_schema(conn)
    out: list[dict[str, Any]] = []
    for t in TERMS:
        col = collision_check(conn, t["term_key"])
        if not col["free"]:
            out.append({"term_key": t["term_key"], "ok": False,
                        "code": "NAME_COLLISION", "collision": col,
                        "why": ("the name is already carried by %s"
                                % (col["tables"] or col["other_terms"]))})
            continue
        r = tr.add_term(conn, t["term_key"], definition=t["definition"],
                        cite_ref=t["cite_ref"], term_kind=t["term_kind"],
                        alias_list=t["alias_list"] or None, is_active=1)
        out.append(dict(r, term_key=t["term_key"], collision=col))
    return {"ok": all(r.get("ok") for r in out), "terms": out,
            "registered": sum(1 for r in out if r.get("ok")),
            "refused": [r for r in out if not r.get("ok")]}


# --------------------------------------------------------------------------
# the capability
# --------------------------------------------------------------------------
def register_capability(conn: sqlite3.Connection) -> dict[str, Any]:
    """`llm.goal_inference` as a capability of kind `thinking`."""
    if not _table_exists(conn, "capability_registry"):
        return {"ok": False, "code": "NO_CAPABILITY_TABLE"}
    ex = conn.execute("SELECT capability_id, capability_kind, module_id "
                      "FROM capability_registry WHERE capability_key = ?",
                      (CAPABILITY_KEY,)).fetchone()
    if ex:
        return {"ok": True, "created": False, "capability_id": int(ex["capability_id"]),
                "capability_kind": str(ex["capability_kind"]),
                "module_id": int(ex["module_id"])}
    mod = conn.execute("SELECT module_id, module_key FROM module_registry "
                       "WHERE module_key = ?", (OWNING_MODULE_KEY,)).fetchone()
    if not mod:
        return {"ok": False, "code": "NO_OWNING_MODULE",
                "message": "no module_registry row with module_key=%r"
                           % OWNING_MODULE_KEY}
    kinds = [r[0] for r in conn.execute(
        "SELECT kind_key FROM capability_kind_registry")]
    if CAPABILITY_KIND not in kinds:
        return {"ok": False, "code": "BAD_CAPABILITY_KIND",
                "message": ("%r is not a declared capability_kind; declared: %s. "
                            "A 6th kind is NOT invented here."
                            % (CAPABILITY_KIND, kinds))}
    cur = conn.execute(
        "INSERT INTO capability_registry (capability_key, name, description, "
        "module_id, is_active, version, capability_kind, gate_ref, why, "
        "probe_tool) VALUES (?,?,?,?,1,'1',?,'NA',?,'NA')",
        (CAPABILITY_KEY, "Goal Inference",
         "DERIVE the goal a chat yields, from the factors the chat RECORDS.",
         int(mod["module_id"]), CAPABILITY_KIND,
         "the router needs a goal before it can match one"))
    conn.commit()
    return {"ok": True, "created": True, "capability_id": int(cur.lastrowid),
            "capability_kind": CAPABILITY_KIND, "module_id": int(mod["module_id"])}


def factors_for(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    """The RECORDED factors of a chat — the inference's only inputs.

    Every factor is a row that EXISTS or a NAMED absence. Nothing is guessed,
    which is what lets `infer()` refuse instead of producing a plausible goal.
    """
    out: dict[str, Any] = {"chat_id": int(chat_id), "recorded": {},
                           "missing": []}
    if not _table_exists(conn, "chat_main"):
        out["missing"].append("chat_main")
        return out
    row = conn.execute("SELECT id, session_id, ide, llm, source FROM chat_main "
                       "WHERE id = ?", (int(chat_id),)).fetchone()
    if not row:
        out["missing"].append("chat_main row")
        return out
    for k in ("session_id", "ide", "llm", "source"):
        v = row[k]
        if v is None or not str(v).strip():
            out["missing"].append("chat_main.%s" % k)
        else:
            out["recorded"]["chat_main.%s" % k] = str(v)
    if _table_exists(conn, "identity_registry"):
        ir = conn.execute("SELECT identity_id, worker_id, channel, why "
                          "FROM identity_registry WHERE chat_id = ?",
                          (int(chat_id),)).fetchone()
        if ir:
            out["recorded"]["identity_registry.worker_id"] = int(ir["worker_id"])
            out["recorded"]["identity_registry.channel"] = str(ir["channel"])
            out["recorded"]["identity_registry.why"] = str(ir["why"])
        else:
            out["missing"].append("identity_registry row for this chat")
    if _table_exists(conn, "chat_center_message"):
        n = conn.execute("SELECT COUNT(*) FROM chat_center_message WHERE chat_id = ?",
                         (int(chat_id),)).fetchone()[0]
        out["recorded"]["chat_center_message.count"] = int(n)
        if not n:
            out["missing"].append("chat_center_message content")
    return out


def infer(conn: sqlite3.Connection, chat_id: int, *,
          infer_with: Callable[[dict[str, Any]], str | None] | None = None
          ) -> dict[str, Any]:
    """The goal a chat yields — or NO_VERDICT naming what is missing.

    `infer_with` is the PROVIDER (the capability's `thinking` step). It is
    INJECTED rather than called here, for two reasons: this module must be
    testable without a live LLM, and a built-in fallback goal would be a
    FABRICATED judgement — the exact defect the no-default rule exists to stop.
    """
    f = factors_for(conn, chat_id)
    if f["missing"] and "chat_main row" in f["missing"]:
        return {"ok": False, "code": "NO_SUCH_CHAT", "chat_id": int(chat_id),
                "why": "no chat_main row", "cite": "measured: chat_main id=%d"
                % int(chat_id)}
    if infer_with is None:
        return {"ok": False, "code": "NO_INFERENCE_PROVIDER",
                "chat_id": int(chat_id),
                "why": ("no provider was supplied, so no goal was inferred. The "
                        "capability %r is declared but the `thinking` step must "
                        "be served by a provider; a built-in default would be a "
                        "fabricated goal" % CAPABILITY_KEY),
                "recorded": f["recorded"], "missing": f["missing"],
                "cite": "measured: infer_with is None"}
    try:
        goal = infer_with(f)
    except Exception as exc:  # a provider failure is NOT a goal
        return {"ok": False, "code": "PROVIDER_FAILED", "chat_id": int(chat_id),
                "why": "%s: %s" % (type(exc).__name__, exc),
                "cite": "measured: infer_with raised"}
    text = str(goal or "").strip()
    if not text or text.upper() == "NA":
        return {"ok": False, "code": "NO_VERDICT", "chat_id": int(chat_id),
                "why": ("the provider returned no goal (%r), which is a real "
                        "outcome, not a pass" % goal),
                "recorded": f["recorded"], "missing": f["missing"],
                "cite": "measured: infer_with returned %r" % (goal,)}
    return {"ok": True, "chat_id": int(chat_id), "goal": text,
            "recorded": f["recorded"], "missing": f["missing"],
            "inferred_by": CAPABILITY_KEY,
            "cite": "measured: infer_with returned %r" % text}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    terms = []
    if _table_exists(conn, "terminology_registry"):
        terms = [dict(r) for r in conn.execute(
            "SELECT term_id, term_key, term_kind, cite_ref FROM "
            "terminology_registry WHERE term_key IN (?,?,?)",
            tuple(t["term_key"] for t in TERMS))]
    cap = None
    if _table_exists(conn, "capability_registry"):
        r = conn.execute("SELECT capability_id, capability_kind, module_id "
                         "FROM capability_registry WHERE capability_key = ?",
                         (CAPABILITY_KEY,)).fetchone()
        cap = dict(r) if r else None
    return {"ok": True, "terms_registered": terms,
            "collisions": {t["term_key"]: collision_check(conn, t["term_key"])
                           for t in TERMS},
            "capability": cap,
            "capability_declared_but_missing": cap is None,
            "kinds": [r[0] for r in conn.execute(
                "SELECT kind_key FROM capability_kind_registry ORDER BY kind_key")]
            if _table_exists(conn, "capability_kind_registry") else []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--register-terms", action="store_true")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.register_terms or args.register:
            res = register_terms(conn)
            print("TERMS: registered=%d  refused=%d"
                  % (res["registered"], len(res["refused"])))
            for t in res["terms"]:
                print("   %-16s ok=%-5s %s" % (t["term_key"], t.get("ok"),
                                               t.get("code") or "registered"))
                if t.get("ok") and t.get("collision"):
                    print("        collision-free: tables=%s derivations=%s other=%s"
                          % (t["collision"]["tables"],
                             t["collision"]["derivations"],
                             t["collision"]["other_terms"]))
            if args.register:
                c = register_capability(conn)
                print("CAPABILITY %s: %s" % (CAPABILITY_KEY, c))
            return 0 if res["ok"] else 2
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        print("declared capability_kinds: %s" % res["kinds"])
        print("capability %s: %s" % (CAPABILITY_KEY, res["capability"]))
        print("terms registered: %s" % [t["term_key"] for t in res["terms_registered"]])
        for k, v in res["collisions"].items():
            print("   name %-16s free=%-5s tables=%s derivations=%s other=%s"
                  % (k, v["free"], v["tables"], v["derivations"],
                     v["other_terms"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
