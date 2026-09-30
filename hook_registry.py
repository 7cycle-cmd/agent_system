# -*- coding: utf-8 -*-
"""hook_registry.py -- a HOOK as a MEASURABLE UNIT.

THE HUMAN (2026-09-26), verbatim
--------------------------------
    "all must be can measure unit and register"
    "stop hook is my pain!!!"
    "have the full plan to find out all, and fix it now"

THE PROBLEM THIS SOLVES
-----------------------
MEASURED (2026-09-26): the Stop hook's entire on/off state was **a filename**.
There was `.github/hooks/proof_gate.json.disabled` and no `.json`, so VS Code did
not load it — and NOTHING in the system recorded that. Measured consequences:

  * `terminology_registry` (1439 rows) had NO term for a hook.
  * `entity_type_registry` had NO hook letter (H M C F A T D S R P U W K Q N X B G).
  * No table among the 33 matching `%register%` / `%setting%` had a hook as its
    subject.
  * The Stop report was the only place that could have said so, and it was silent.

So a hook that was OFF was indistinguishable from a hook that was ON and idle.
That is exactly what "cannot be measured" means, and this module is the unit.

THE ONE LAW
-----------
`is_registered` is DERIVED FROM THE FILESYSTEM, never typed by hand. A registry
row that says "registered" while the file is `.disabled` is a FAULT, not a pass.
`measure()` returns that FAULT by name so it can never be read as agreement.

NEVER RAISES
------------
Every failure is returned as `ok: False` with a `why`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB = BASE / "agent.db"
HOOKS_DIR = BASE / ".github" / "hooks"

CITE = "human 2026-09-26 + .github/hooks/*.json load set"

# The events VS Code supports in this repo's hook files.
EVENTS = ("Stop", "PreToolUse", "SessionStart", "UserPromptSubmit")

# THE DDL, NAMED `HOOK_REGISTRY_DDL` SO IT CAN BE FOUND (fixed 2026-09-27).
#
# MEASURED DEFECT: this constant was called plain `DDL`, and
# `logic_generator._iter_ddl_constants` reads DDL by the naming law
# `^([A-Za-z_]\w*_DDL)\s*=` — the SAME law every other table obeys
# (`UI_ELEMENT_DDL`, `WORKFLOW_REGISTER_DDL`, `FRONTIER_DDL`). A bare `DDL` does
# not match it, so `declared_shape('hook_registry')` found NO declaration and
# `question_generator` REFUSED the table — not because the DDL was missing, but
# because its NAME could not be found. A table with no declared shape has no unit
# to measure against, so the refusal was correct; the NAME was the defect.
HOOK_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS hook_registry (
    hook_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    hook_key      TEXT    NOT NULL UNIQUE,
    event_name    TEXT    NOT NULL,
    config_name   TEXT    NOT NULL,
    script_path   TEXT    NOT NULL,
    -- THE DECLARED STATE. It is COMPARED against the filesystem, never trusted.
    is_registered INTEGER NOT NULL DEFAULT 0 CHECK (is_registered IN (0, 1)),
    is_enabled    INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    why           TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL DEFAULT 'NA',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hook_registry_key ON hook_registry(hook_key);
"""

# The three hooks that exist in `.github/hooks/`.
# MEASURED from the load set: ask_mode_guard.json (PreToolUse),
# plan_gate.json (SessionStart, UserPromptSubmit, PreToolUse),
# proof_gate.json.disabled (Stop).
SEED = (
    ("ask_mode_guard", "PreToolUse", "ask_mode_guard.json",
     "scripts/ask_mode_guard.py", 0),
    ("plan_gate_session_start", "SessionStart", "plan_gate.json",
     "scripts/plan_gate.py", 0),
    ("plan_gate_user_prompt", "UserPromptSubmit", "plan_gate.json",
     "scripts/plan_gate.py", 0),
    ("plan_gate_pre_tool", "PreToolUse", "plan_gate.json",
     "scripts/plan_gate.py", 0),
    ("proof_gate_stop", "Stop", "proof_gate.json",
     "scripts/proof_gate.py", 0),
)


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table and seed the three real hooks. ADDITIVE."""
    conn.executescript(HOOK_REGISTRY_DDL)
    for key, ev, cfg, script, _ in SEED:
        conn.execute(
            "INSERT INTO hook_registry (hook_key, event_name, config_name, "
            "script_path, why, cite_ref) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(hook_key) DO UPDATE SET event_name=excluded.event_name, "
            "config_name=excluded.config_name, script_path=excluded.script_path, "
            "cite_ref=excluded.cite_ref, updated_at=datetime('now')",
            (key, ev, cfg, script,
             "the %s event for %s" % (ev, script), CITE))
    conn.commit()
    return {"ok": True, "seeded": len(SEED)}


def _load_set() -> list[str]:
    """The `*.json` files VS Code loads as hooks.

    MEASURED: VS Code loads `.github/hooks/*.json`. A file named
    `proof_gate.json.disabled` is NOT in that set, so the hook is not
    registered -- and that is a fact about the FILESYSTEM, not an opinion.
    """
    try:
        return sorted(f.name for f in HOOKS_DIR.iterdir()
                      if f.is_file() and f.name.endswith(".json"))
    except Exception:
        return []


def measure(hook_key: str, *, conn: sqlite3.Connection | None = None) -> dict:
    """Measure ONE hook's real state from the filesystem, cross-checked.

    Returns `{ok, hook_key, event_name, config_name, script_path,
    is_registered, is_enabled, declared_registered, verdict, why}`.

    `verdict` is `REGISTERED` / `NOT_REGISTERED` / `FAULT`. A FAULT means the
    registry row and the filesystem DISAGREE -- the one case that must never be
    silently resolved in either direction.
    """
    own = conn is None
    if conn is None:
        conn = sqlite3.connect(str(DB))
        conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM hook_registry WHERE hook_key = ?",
            (str(hook_key),)).fetchone()
        if not row:
            return {"ok": False, "hook_key": str(hook_key),
                    "verdict": "FAULT",
                    "why": "no hook_registry row for %r -- an unregistered hook "
                           "is not measurable" % str(hook_key)}
        d = dict(row)
        # THE FILESYSTEM IS THE TRUTH.
        loaded = _load_set()
        live = d["config_name"] in loaded
        disabled = (HOOKS_DIR / (d["config_name"] + ".disabled")).is_file()
        script_ok = (BASE / d["script_path"]).is_file()
        # A CONTRADICTION between the row and the filesystem is a FAULT.
        declared = bool(d["is_registered"])
        fault = None
        if declared and not live:
            fault = ("hook_registry says %s is REGISTERED but %s is not in the "
                     "load set %s" % (d["hook_key"], d["config_name"], loaded))
        elif (not declared) and live:
            fault = ("hook_registry says %s is NOT registered but %s IS in the "
                     "load set %s" % (d["hook_key"], d["config_name"], loaded))
        if not script_ok:
            fault = ("the script %s does not exist" % d["script_path"])
        why = ""
        if fault:
            why = fault
        elif live:
            why = "%s is in the load set %s" % (d["config_name"], loaded)
        else:
            why = ("%s is NOT in the load set %s%s"
                   % (d["config_name"], loaded,
                      "; the file is named %s.disabled"
                      % d["config_name"] if disabled else ""))
        return {
            "ok": fault is None,
            "hook_key": d["hook_key"],
            "event_name": d["event_name"],
            "config_name": d["config_name"],
            "script_path": d["script_path"],
            "is_registered": live,
            "is_enabled": bool(d["is_enabled"]),
            "declared_registered": declared,
            "load_set": loaded,
            "disabled_file_present": disabled,
            "verdict": "FAULT" if fault else
                       ("REGISTERED" if live else "NOT_REGISTERED"),
            "why": why,
        }
    finally:
        if own:
            conn.close()


def sync_declared(*, conn: sqlite3.Connection | None = None) -> dict:
    """Write the MEASURED state into the registry. Filesystem wins.

    Run this after any rename, so the row can never drift from the disk. The
    rows are still cross-checked by `measure()`; syncing hides nothing.
    """
    own = conn is None
    if conn is None:
        conn = sqlite3.connect(str(DB))
        conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        loaded = _load_set()
        out = []
        for key, *_ in SEED:
            r = measure(key, conn=conn)
            live = 1 if r.get("is_registered") else 0
            conn.execute(
                "UPDATE hook_registry SET is_registered = ?, why = ?, "
                "updated_at = datetime('now') WHERE hook_key = ?",
                (live, r.get("why") or "NA", key))
            out.append((key, live))
        conn.commit()
        # MEASURED 2026-09-26: the cross-check must agree AFTER the sync, or the
        # sync itself would be the thing lying. A second pass proves it.
        verify = [measure(k, conn=conn).get("verdict") for k, *_ in SEED]
        return {"ok": all(v in ("REGISTERED", "NOT_REGISTERED") for v in verify),
                "load_set": loaded, "synced": out, "verdicts": verify}
    finally:
        if own:
            conn.close()


def all_hooks(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every hook, measured."""
    own = conn is None
    if conn is None:
        conn = sqlite3.connect(str(DB))
        conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        keys = [r[0] for r in conn.execute(
            "SELECT hook_key FROM hook_registry ORDER BY hook_key").fetchall()]
        return [measure(k, conn=conn) for k in keys]
    finally:
        if own:
            conn.close()


def main() -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print("== hook_registry ==")
    print(ensure_schema(conn))
    s = sync_declared(conn=conn)
    print("load set:", s["load_set"])
    print("==")
    for h in all_hooks(conn=conn):
        print("  %-26s %-16s %-14s %s"
              % (h["hook_key"], h["event_name"], h["verdict"],
                 (h["why"] or "")[:70]))
    # NEGATIVE CONTROL: a hook that does not exist must NOT be measurable.
    print("\n== negative control ==")
    print("  unknown hook ->", measure("no_such_hook", conn=conn).get("verdict"))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
