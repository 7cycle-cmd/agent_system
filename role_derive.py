"""role_derive.py — DERIVE a worker's role from DECLARED evidence, or REFUSE.

THE USER'S QUESTION (2026-09-24)
--------------------------------
    "6 個 worker 全部 NO_ROLE — can by evidence to get it by logic generation?
     example, vscode is the code writing software"

and the follow-up:

    "be skill, when new role happen can be auto forever by API"

THE EXAMPLE WAS TESTED AGAINST THE REGISTER, AND IT CORRECTS THE METHOD
---------------------------------------------------------------------
`vscode` is offered as "the code writing software". The register says:

    "The IDE that owns the conversation: Visual Studio Code. It qualifies the
     conversation so it cannot be read as the chat system's."   (mode_attest.py:258)

and its `physical_path` is `'vscode'`, which is NOT a file. So a derivation that
followed the DESCRIPTION would have produced `writer` for a term whose declared
subject is CONVERSATION IDENTITY. **RULE: derive from the REGISTER, never from a
description.**

WHAT EVIDENCE ACTUALLY SUPPORTS A DERIVATION (measured)
-------------------------------------------------------
* `capability_registry.capability_kind` — `code` / `eye` / `hand` / `thinking` /
  `voice`, EACH with a definition in `capability_kind_registry`. **A declared
  vocabulary, so it is strong evidence.**
* `capability_registry.gate_ref` — **NA on every row**, so "its output GATES
  another worker" (the definition of `verifier`) is NOT derivable. `verifier` is
  therefore reported as MISSING EVIDENCE, never guessed.
* `worker_registry.worker_type` — free text with NO `worker_type_registry`, so it is
  NOT a declared vocabulary and is not used as a rule's sole basis.
* `physical_path` — a real file for 5 workers and `'vscode'` for 1; a WEAK signal.

FAIL CLOSED
-----------
An undecidable worker stays `NO_ROLE`. `needs_evidence()` names what WOULD settle
it, so a gap is a WORK ITEM rather than a mystery.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

MAX_ROUNDS = 10

STOP_ALL_DERIVED = "ALL_DERIVED"
STOP_NO_PROGRESS = "NO_PROGRESS"
STOP_MAX_ROUNDS = "MAX_ROUNDS_REACHED"
STOP_BLOCKED = "BLOCKED_NEEDS_EVIDENCE"

# ---------------------------------------------------------------------------
# THE RULES, AS DATA. Each rule names the EVIDENCE it reads and the ROLE it yields.
#
# The KIND -> ROLE map is the derivation. It follows the kind's OWN definition in
# `capability_kind_registry`, which is why it is strong evidence rather than a
# naming convention:
#
#   thinking : "reads text or an image and RETURNS A JUDGEMENT"  -> a JUDGEMENT is a
#              CLAIM, and a claim with no artifact needs a verifier (that is the
#              user's own ruling). So a `thinking` provider is a RESEARCHER.
#   eye/voice: "CAPTURES pixels" / "SPEAKS or LISTENS"           -> an OBSERVATION is
#              also a claim about the world -> RESEARCHER.
#   code     : "produces or gates SOURCE CODE"                   -> produces an
#              ARTIFACT -> WRITER.
#   hand     : "ACTS: it runs a command or drives another application" -> it
#              produces a CHANGE -> WRITER.
#
# NOTE WHAT IS ABSENT: nothing yields `verifier`. MEASURED: `gate_ref` is NA on
# every capability row, so "this output GATES another worker" has no evidence yet.
# A rule for it would be a guess, and a guess is what this module exists to refuse.
# ---------------------------------------------------------------------------
KIND_TO_ROLE: dict[str, str] = {
    "thinking": "researcher",
    "eye": "researcher",
    "voice": "researcher",
    "code": "writer",
    "hand": "writer",
}

RULES: dict[str, dict[str, Any]] = {
    "R1_capability_kind": {
        "evidence": "worker_registry.capability_ref -> capability_registry.capability_kind",
        "yields": "the role mapped from the kind's OWN declared definition",
        "why": ("the kind vocabulary is DECLARED (capability_kind_registry) and each "
                "definition states whether its output is an ARTIFACT or a JUDGEMENT"),
    },
    "R2_provider_edits_a_codebase": {
        "evidence": "worker_type + physical_path",
        "yields": "writer",
        "why": ("a provider that edits a CODEBASE produces an artifact; it is NOT "
                "trusted alone, because `vscode`'s physical_path is not a file and "
                "its register term is about conversation identity — MEASURED"),
    },
}

# Evidence that WOULD settle a worker the rules cannot decide.
EVIDENCE_NEEDED: dict[str, str] = {
    "NO_CAPABILITY_EVIDENCE": ("register the worker's `capability_ref` in "
                               "capability_registry (with a capability_kind), or "
                               "state its `gate_ref` if its output gates another "
                               "worker"),
    "NO_KIND_FOR_CAPABILITY": ("set `capability_registry.capability_kind` for the "
                               "worker's capability, choosing from "
                               "capability_kind_registry"),
    "NON_CODE_PHYSICAL_PATH": ("the worker's `physical_path` is not a file, so it "
                               "cannot be read as an artifact producer; register "
                               "the capability it SERVES instead"),
}


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [d[1] for d in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.OperationalError:
        return []


def declared_kinds(conn: sqlite3.Connection) -> dict[str, str]:
    """The kind vocabulary READ FROM THE DB, with each definition.

    Read, never hard-coded: if a 6th kind is declared, the derivation sees it —
    though it yields no role until a rule maps it, which is REPORTED rather than
    silently defaulted.
    """
    if not _cols(conn, "capability_kind_registry"):
        return {}
    return {str(r["kind_key"]): str(r["definition"]) for r in conn.execute(
        "SELECT kind_key, definition FROM capability_kind_registry")}


def derive_role(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """Derive a worker's role from DECLARED evidence, or REFUSE with a named reason.

    Returns `{ok, worker_key, role|None, rule, evidence, cite, refused, why}`.
    `role` is None whenever the evidence does not decide, and `refused` names why.
    It NEVER guesses, and it NEVER returns a role without a rule and a citation.
    """
    w = conn.execute(
        "SELECT worker_id, worker_key, worker_type, capability_ref, physical_path, "
        "role_id FROM worker_registry WHERE worker_key = ?",
        (str(worker_key),)).fetchone()
    if w is None:
        return {"ok": False, "reason": "NO_SUCH_WORKER", "worker_key": str(worker_key)}

    out: dict[str, Any] = {
        "ok": True, "worker_key": str(w["worker_key"]),
        "worker_type": str(w["worker_type"] or ""),
        "capability_ref": str(w["capability_ref"] or ""),
        "physical_path": str(w["physical_path"] or ""),
        "current_role_id": (str(w["role_id"]) if w["role_id"] is not None else None),
        "role": None, "rule": None, "evidence": None, "cite": None,
        "refused": None, "why": None,
    }

    # ---- R1: the DECLARED kind vocabulary -----------------------------------
    ref = str(w["capability_ref"] or "").strip()
    cap = conn.execute(
        "SELECT capability_key, capability_kind, gate_ref FROM capability_registry "
        "WHERE capability_key = ?", (ref,)).fetchone() if ref else None
    kinds = declared_kinds(conn)
    if cap is None:
        out["refused"] = "NO_CAPABILITY_EVIDENCE"
        out["why"] = ("capability_ref %r is not in capability_registry, so there is "
                      "nothing DECLARED to derive from" % ref)
    else:
        kind = str(cap["capability_kind"] or "").strip()
        if not kind or kind == "NA":
            out["refused"] = "NO_KIND_FOR_CAPABILITY"
            out["why"] = ("capability %r declares no capability_kind" % ref)
        elif kind not in kinds:
            out["refused"] = "NO_KIND_FOR_CAPABILITY"
            out["why"] = ("kind %r is not declared in capability_kind_registry" % kind)
        elif kind in KIND_TO_ROLE:
            out["role"] = KIND_TO_ROLE[kind]
            out["rule"] = "R1_capability_kind"
            out["evidence"] = ("capability_registry.capability_kind=%r for %r"
                               % (kind, ref))
            out["cite"] = "measured: capability_kind_registry.%s definition" % kind
            out["why"] = RULES["R1_capability_kind"]["why"]
            out["kind"] = kind
            out["kind_definition"] = kinds.get(kind)
            # A `verifier` would need gate evidence, which is MEASURED absent.
            if out["role"] == "verifier" and str(cap["gate_ref"] or "NA") == "NA":
                out["role"] = None
                out["refused"] = "NEEDS_GATE_EVIDENCE"
                out["why"] = ("verifier needs 'its output GATES another worker', but "
                              "gate_ref is NA — not derivable")
            return out
        else:
            out["refused"] = "NO_RULE_FOR_KIND"
            out["why"] = ("kind %r is declared but no rule maps it to a role; add a "
                          "rule rather than defaulting" % kind)

    # ---- R2: a provider that edits a CODEBASE (weak, and NEVER alone for a role
    #          whose register term is about something else) ---------------------
    path = str(w["physical_path"] or "").strip()
    wtype = str(w["worker_type"] or "").strip()
    if out["role"] is None and wtype.startswith("provider."):
        if path.endswith(".py") or path.endswith(".js") or path.endswith(".ts"):
            out["role"] = "writer"
            out["rule"] = "R2_provider_edits_a_codebase"
            out["evidence"] = "worker_type=%r + physical_path=%r (a code file)" % (
                wtype, path)
            out["cite"] = "measured: worker_registry.physical_path is a code file"
            out["why"] = RULES["R2_provider_edits_a_codebase"]["why"]
            return out
        out["refused"] = "NON_CODE_PHYSICAL_PATH"
        out["why"] = ("worker_type=%r but physical_path=%r is not a code file; the "
                      "register term for that name does not describe code, so the "
                      "description cannot be used (MEASURED: vscode is a "
                      "conversation-identity qualifier, mode_attest.py:258)"
                      % (wtype, path))
        return out

    if out["role"] is None and out["refused"] is None:
        out["refused"] = "NO_RULE_MATCHED"
        out["why"] = ("no rule's evidence is present for this worker, so its role "
                      "cannot be derived")
    return out


def derive_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Derive for EVERY worker, and count the refusals."""
    workers = [str(r["worker_key"]) for r in conn.execute(
        "SELECT worker_key FROM worker_registry ORDER BY worker_id")]
    results = [derive_role(conn, w) for w in workers]
    derived = [r for r in results if r.get("role")]
    refused = [r for r in results if not r.get("role")]
    return {"ok": True, "workers": len(results), "derived": len(derived),
            "refused": len(refused), "results": results,
            "by_role": {role: sum(1 for r in results if r.get("role") == role)
                        for role in sorted(set(KIND_TO_ROLE.values()))},
            "would_be_red_if": "a role is returned without a rule and a citation"}


def needs_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    """WHAT WOULD SETTLE each undecided worker. A gap becomes a WORK ITEM."""
    d = derive_all(conn)
    items: list[dict[str, Any]] = []
    for r in d["results"]:
        if r.get("role"):
            continue
        code = str(r.get("refused") or "NO_RULE_MATCHED")
        items.append({
            "worker_key": r["worker_key"],
            "refused": code,
            "evidence_needed": EVIDENCE_NEEDED.get(code, "state the missing evidence"),
            "why": r.get("why"),
        })
    return {"ok": True, "count": len(items), "items": items,
            "kinds_declared": sorted(declared_kinds(conn)),
            "kinds_with_a_rule": sorted(KIND_TO_ROLE),
            "kinds_withOUT_a_rule": sorted(
                set(declared_kinds(conn)) - set(KIND_TO_ROLE)),
            "verifier_evidence_present": any(
                str(r["gate_ref"] or "NA") != "NA" for r in conn.execute(
                    "SELECT gate_ref FROM capability_registry")),
            "would_be_red_if": "a worker that could be derived is left undecided"}


def until_all_derived(conn: sqlite3.Connection, *, max_rounds: int = MAX_ROUNDS,
                      assign: bool = False,
                      cite_ref: str = "role_derive.py:1") -> dict[str, Any]:
    """AUTO-FOREVER, made safe: BOUNDED with a NAMED stop reason.

    Each round DERIVES; when `assign` is set it ASSIGNS only a derived role through
    the ONE cited write path. It cannot run forever: the stop is one of
    `ALL_DERIVED`, `NO_PROGRESS`, `MAX_ROUNDS_REACHED`, `BLOCKED_NEEDS_EVIDENCE`.
    """
    import role_right_registry as rrr

    rounds: list[dict[str, Any]] = []
    prev: tuple | None = None
    stop = STOP_MAX_ROUNDS
    for n in range(1, int(max_rounds) + 1):
        d = derive_all(conn)
        refused_keys = tuple(sorted(r["worker_key"] for r in d["results"]
                                    if not r.get("role")))
        pending = [r for r in d["results"] if r.get("role")]
        assigned: list[str] = []
        if assign:
            for r in pending:
                # A worker that ALREADY has this role is left alone.
                if r.get("current_role_id") == r["role"]:
                    continue
                res = rrr.declare_role(conn, r["worker_key"], r["role"],
                                       cite_ref="%s (%s)" % (r["cite"] or "", cite_ref))
                if res.get("ok"):
                    assigned.append("%s->%s" % (r["worker_key"], r["role"]))
        rounds.append({"round": n, "derived": d["derived"], "refused": d["refused"],
                       "assigned": assigned,
                       "refused_keys": list(refused_keys)})
        if d["refused"] == 0 and d["derived"] == d["workers"]:
            stop = STOP_ALL_DERIVED
            break
        # A worker the RULES cannot decide is blocked by EVIDENCE, not by effort.
        # MEASURED BUG IN THIS RUNNER'S FIRST VERSION: `BLOCKED` required
        # `derived == 0`, so 4 derivable + 2 evidence-blocked workers reported
        # NO_PROGRESS — which implies "the loop tried and nothing moved". It did not
        # try to make those 2 move: their evidence is absent, and the honest name
        # for that is BLOCKED_NEEDS_EVIDENCE. The distinction matters because it
        # tells the reader whether to look at the LOOP or at the DATA.
        cannot_change = refused_keys and (
            not assign
            or all(r.get("current_role_id") == r.get("role")
                   for r in d["results"] if r.get("role")))
        if cannot_change:
            stop = STOP_BLOCKED
            break
        if prev is not None and refused_keys == prev and not assigned:
            stop = STOP_NO_PROGRESS
            break
        prev = refused_keys
    else:
        stop = STOP_MAX_ROUNDS

    final = derive_all(conn)
    return {"stop_reason": stop, "ran": len(rounds), "max_rounds": int(max_rounds),
            "rounds": rounds, "final": {"workers": final["workers"],
                                        "derived": final["derived"],
                                        "refused": final["refused"]},
            "named_stop": stop in (STOP_ALL_DERIVED, STOP_NO_PROGRESS,
                                   STOP_MAX_ROUNDS, STOP_BLOCKED)}


# ---------------------------------------------------------------------------
# THE API — the same APIRouter shape `terminology_api.py` and `object_door.py` use.
# ---------------------------------------------------------------------------

def register(app: Any) -> None:
    """Mount the derivation on a FastAPI app."""
    try:
        from fastapi import APIRouter
    except Exception:  # pragma: no cover
        return
    router = APIRouter()

    def _conn() -> sqlite3.Connection:
        c = sqlite3.connect(str(DB_PATH))
        c.row_factory = sqlite3.Row
        return c

    @router.get("/api/role/derive-all")
    def _derive_all() -> Any:
        c = _conn()
        try:
            return derive_all(c)
        finally:
            c.close()

    @router.get("/api/role/derive")
    def _derive(worker_key: str) -> Any:
        c = _conn()
        try:
            return derive_role(c, worker_key)
        finally:
            c.close()

    @router.get("/api/role/needs-evidence")
    def _needs() -> Any:
        c = _conn()
        try:
            return needs_evidence(c)
        finally:
            c.close()

    @router.get("/api/role/run")
    def _run(assign: bool = False, max_rounds: int = MAX_ROUNDS) -> Any:
        c = _conn()
        try:
            return until_all_derived(c, max_rounds=max_rounds, assign=assign)
        finally:
            c.close()

    app.include_router(router)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--derive-all", action="store_true")
    ap.add_argument("--derive", metavar="WORKER_KEY", default=None)
    ap.add_argument("--needs-evidence", action="store_true")
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--assign", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.rules:
            out = {"ok": True, "kind_to_role": KIND_TO_ROLE,
                   "rules": RULES, "declared_kinds": declared_kinds(conn),
                   "note": ("no rule yields `verifier`: MEASURED, gate_ref is NA on "
                            "every capability row, so its evidence is absent")}
        elif a.run:
            out = until_all_derived(conn, assign=bool(a.assign))
        elif a.needs_evidence:
            out = needs_evidence(conn)
        elif a.derive:
            out = derive_role(conn, a.derive)
        else:
            out = derive_all(conn)
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.rules:
        print("kind -> role:")
        for k, v in sorted(out["kind_to_role"].items()):
            print("   %-10s -> %s" % (k, v))
        print("declared kinds without a rule: %s"
              % (sorted(set(out["declared_kinds"]) - set(out["kind_to_role"])) or "none"))
        print()
        print("NOTE: %s" % out["note"])
    elif a.run:
        print("stop_reason: %s" % out["stop_reason"])
        print("ran rounds : %d of %d" % (out["ran"], out["max_rounds"]))
        for r in out["rounds"]:
            print("   round %d: derived=%d refused=%d assigned=%s"
                  % (r["round"], r["derived"], r["refused"], r["assigned"] or "-"))
        print("final      : %s" % out["final"])
    elif a.needs_evidence:
        print("workers needing evidence: %d" % out["count"])
        for i in out["items"]:
            print("   %-18s %-26s %s" % (i["worker_key"], i["refused"],
                                         i["evidence_needed"][:58]))
        print()
        print("kinds WITH a rule    : %s" % out["kinds_with_a_rule"])
        print("kinds WITHOUT a rule : %s" % (out["kinds_withOUT_a_rule"] or "none"))
        print("verifier evidence present: %s" % out["verifier_evidence_present"])
    elif a.derive:
        print("worker   : %s" % out.get("worker_key"))
        print("role     : %s" % (out.get("role") or "(NOT DERIVED)"))
        print("rule     : %s" % out.get("rule"))
        print("evidence : %s" % out.get("evidence"))
        print("cite     : %s" % out.get("cite"))
        if out.get("refused"):
            print("REFUSED  : %s" % out["refused"])
            print("why      : %s" % out["why"])
    else:
        print("workers: %d | derived=%d refused=%d"
              % (out["workers"], out["derived"], out["refused"]))
        for r in out["results"]:
            print("   %-18s -> %-11s %s"
                  % (r["worker_key"], r.get("role") or "NO_ROLE",
                     r.get("rule") or r.get("refused")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
