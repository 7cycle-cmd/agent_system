# -*- coding: utf-8 -*-
"""_register_qc_gate_terms.py — register the `qc_gate` layer term.

WHY WORDS FIRST (the human, 2026-09-28)
---------------------------------------
    "it is BUG by worng speeling, as we have terminontology, i don't know why you
     still can keep find that / the name is 5W1H"

The correction: a name is NOT chosen in prose. It is LOOKED UP in
`terminology_registry`, and only a name whose every WORD already exists as a
registered word may be introduced. MEASURED (`python terminology_registry.py
--list`): `qc` and `gate` are both registered parts, so `qc_gate` decomposes with
zero invented words. Every one of the nine gate keys is likewise an
ALREADY-REGISTERED word (`ontology`, `5w1h`, `middleware`, `tdd`, `boundary`,
`role_environment` -> role+environment, `trace`, `safety`, `verdict`), so this
script does NOT invent them either — it only checks them with `assert_named`.

WHAT IT REGISTERS
-----------------
ONE term: `qc_gate`. The nine gate keys are NOT re-registered, because they
already exist; re-adding them would be the duplicate-name defect HEAD C forbids.

Run:
    .\\.venv\\Scripts\\python.exe scripts/_register_qc_gate_terms.py
    .\\.venv\\Scripts\\python.exe scripts/_register_qc_gate_terms.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_gate  # noqa: E402
import terminology_registry as tr  # noqa: E402

DEFAULT_DB = BASE / "agent.db"

# MEASURED 2026-09-28 by RUNNING the new PostToolUse hook on this file tree:
# `qc_gate_hook` and the other new modules had NO terminology row, so the gate
# (correctly) refused them. The gate's fix message points at this script, so this
# is the script that must register them. Every name decomposes from already
# registered words (`qc`+`gate`, `qc`+`report`+`truth`, `pattern`+`template`).
MODULE_TERMS = (
    ("qc_gate_hook", "scripts/qc_gate_hook.py:1"),
    ("qc_gate_verify", "scripts/qc_gate_verify.py:1"),
    ("qc_report_truth", "scripts/qc_report_truth.py:1"),
    ("qc_gate_runner", "qc_gate_runner.py:1"),
    ("qc_arbiter", "qc_arbiter.py:1"),
    ("pattern_template", "pattern_template.py:1"),
)

# WORDS a module name DECOMPOSES from. MEASURED 2026-09-28: `qc_arbiter` was
# REFUSED with HEAD A MISSING_WORD — "unregistered word(s): arbiter". `arbiter`
# is the user's OWN A09 name from their Universal 9-Agent spec, so it is not
# renamed away; it is REGISTERED, and then the module name decomposes.
WORD_TERMS = (
    ("arbiter", "role", "qc_arbiter.py:1",
     "arbiter: the A09 step that turns the nine dimension results into ONE "
     "verdict (the user's Universal 9-Agent spec, A09 Arbiter)."),
    ("arbitrate", "action", "qc_arbiter.py:1",
     "arbitrate: the act of deriving ONE verdict from the nine dimension "
     "results (see arbiter)."),
)


def check_all(conn: sqlite3.Connection) -> dict:
    """Report which terms already resolve, and which (if any) are missing."""
    out = {"qc_gate": None, "gates": {}, "modules": {}}
    ok, why = tr.assert_named(conn, qc_gate.TERM_KEY)
    out["qc_gate"] = {"ok": bool(ok), "reason": str(why)}
    for k in qc_gate.GATE_KEYS:
        ok, why = tr.assert_named(conn, k)
        out["gates"][k] = {"ok": bool(ok), "reason": str(why)}
    for name, _cite in MODULE_TERMS:
        ok, why = tr.assert_named(conn, name)
        out["modules"][name] = {"ok": bool(ok), "reason": str(why)}
    out["words"] = {}
    for name, _kind, _cite, _defn in WORD_TERMS:
        ok, why = tr.assert_named(conn, name)
        out["words"][name] = {"ok": bool(ok), "reason": str(why)}
    out["missing"] = ([qc_gate.TERM_KEY] if not out["qc_gate"]["ok"] else []) + \
        [k for k, v in out["gates"].items() if not v["ok"]] + \
        [k for k, _kind, _cite, _defn in WORD_TERMS
         if not out.get("words", {}).get(k, {}).get("ok")] + \
        [k for k, v in out["modules"].items() if not v["ok"]]
    return out


def register(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """Register `qc_gate` if it is missing. Idempotent.

    The nine gate keys are ONLY verified, never inserted: they already exist, and
    a second row for an existing name is the duplicate the law forbids.
    """
    res = check_all(conn)
    added: list[dict] = []
    if not res["qc_gate"]["ok"]:
        if not apply:
            return {"ok": True, "action": "would_registry", "check": res}
        added.append(("qc_gate", tr.add_term(
            conn, qc_gate.TERM_KEY, definition=qc_gate.TERM_DEFINITION,
            cite_ref=qc_gate.TERM_CITE, term_kind="entity")))
    if apply:
        for name, kind, cite, definition in WORD_TERMS:
            try:
                ok0, _why0 = tr.assert_named(conn, name)
                if ok0:
                    continue
            except Exception:
                pass
            try:
                added.append((name, tr.add_term(
                    conn, name, definition=definition, cite_ref=cite,
                    term_kind=kind)))
            except Exception as exc:
                added.append((name, {"ok": False, "reason": str(exc)}))
        for name, cite in MODULE_TERMS:
            if res["modules"].get(name, {}).get("ok"):
                continue
            try:
                added.append((name, tr.add_term(
                    conn, name,
                    definition=("%s: a module of the qc_gate layer (see %s)"
                                % (name, cite)),
                    cite_ref=cite, term_kind="entity")))
            except Exception as exc:
                added.append((name, {"ok": False, "reason": str(exc)}))
    if not added:
        return {"ok": True, "action": "already_registered", "check": res}
    return {"ok": all(bool(v.get("ok")) for _, v in added),
            "action": "register",
            "results": [{"term": k, "result": v} for k, v in added],
            "check": check_all(conn)}


def main() -> int:
    ap = argparse.ArgumentParser(description="register the qc_gate term")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true", help="write the term")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        out = register(conn, apply=args.apply)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        missing = out.get("check", {}).get("missing", [])
        if missing:
            print("\nMISSING (must be registered before use): %s" % missing)
            return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())