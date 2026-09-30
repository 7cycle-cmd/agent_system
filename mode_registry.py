# -*- coding: utf-8 -*-
"""mode_registry.py — THE MODE + RIGHT SYSTEM, all DB driven.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "+UI  /llm-tasks/worker/right  -> mode : ask / plan / agent
     this is the skill for worker to understand the edge
     -> terminal : -> coding writing : -> plan writing :
     -> plan file location : -> switch mode middleware : 5W1 H
     /llm-tasks/worker/list  + field  mode is apply for this worker or not"
    "all is DB driven"
    "the problem is how to give help before complain not block the activity
     only, how to user friendly for worker is one of the factor in this plan"

MEASURED BEFORE THIS: there was NO mode table anywhere (`mode_registry` -> 0
hits). The rights lived as PROSE in `scripts/plan_gate.py` `RIGHTS`, and the
per-mode skill lists in a FILE (`mode_skills.json`). A fourth mode would have
been a Python or JSON edit — the defect the user has corrected repeatedly
(`subject_kind_registry.py:1-40`, `FACTOR_TEMPLATE_DDL`). So the modes and the
rights are ROWS.

THE SIXTH RIGHT IS THE HELP, NOT A PERMISSION
---------------------------------------------
Five rights are permissions. The sixth, `how_to_proceed`, is WHAT TO DO NEXT.
A deny that only refuses is a wall; a deny that names the next act is a helper.
`redirect()` appends this row to every reason, so the worker is never left
without a way forward. A mode with no help row is a FAULT, like any other gap.

THE WORDING IS NOT HERE
-----------------------
This module contains NO right values and NO help text of its own. It reads
`mode_right_registry`. The SEED below is the only place the initial values live,
and every seeded row carries a `cite_ref` naming WHERE the fact was measured, so
a reader can check it rather than trust it.

CLI
---
    python mode_registry.py --seed
    python mode_registry.py --modes
    python mode_registry.py --rights [--mode plan]
    python mode_registry.py --help-for plan
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

# THE SIX RIGHT KEYS. FIXED, and validated at the WRITE SITE rather than by an
# SQL enum — the same split `skill_5w1h.DIMENSION_NAMES` uses: a FIXED set is a
# tuple a test can assert; an OPEN set (the modes) is a table.
RIGHT_KEYS: tuple[str, ...] = (
    "terminal",
    "coding_writing",
    "plan_writing",
    "plan_file",
    "switch_mode_middleware",
    "how_to_proceed",
)

# The legal VALUES for a permission row. `plan_file` holds a PATH and
# `switch_mode_middleware` a TOOL NAME, so only the three permissions are
# constrained; the three value-rows are free text with a NON-EMPTY rule.
PERMISSION_RIGHTS: tuple[str, ...] = ("terminal", "coding_writing",
                                      "plan_writing")
PERMISSION_VALUES: tuple[str, ...] = ("yes", "no")

# THE HELP ROW IS REQUIRED FOR EVERY MODE. This is the user's requirement, made
# checkable: a deny with no help is a DEFECT.
HELP_RIGHT = "how_to_proceed"

PLAN_ARTIFACT = "qc_evidence/plan_<task_id>.md"
PLAN_TEMPLATE = "docs/snippets/template_plan.md"

# The modes. `mode_skills.json` is the citation: it is where these three were
# declared before this table existed, so nothing is lost by seeding from it.
MODE_SEED: tuple[tuple[str, str, int, str], ...] = (
    ("ask", "ASK", 1, "mode_skills.json:1-25"),
    ("plan", "PLAN", 2, "mode_skills.json:1-25"),
    ("agent", "AGENT", 3, "mode_skills.json:1-25"),
)

# THE MATRIX. Every value below is DERIVED FROM THE GATE'S MEASURED BEHAVIOUR
# (`scripts/plan_gate.py` `redirect()`), and the cite_ref says so. This is what
# makes the page trustworthy: it is not prose, it is the gate's behaviour in
# table form — and `_proof_worker_rights.py` asserts the two agree.
#
# (mode_key, right_key, value_text, why, cite_ref)
MODE_RIGHT_SEED: tuple[tuple[str, str, str, str, str], ...] = (
    # ---- ASK: research + answer. No writes of any kind. ----
    ("ask", "terminal", "no",
     "ASK may not run commands; that belongs to AGENT after a plan",
     "plan_gate.py:578-590 (ASK branch denies every write)"),
    ("ask", "coding_writing", "no",
     "ASK answers in text; product code is written in AGENT",
     "plan_gate.py:578-590"),
    ("ask", "plan_writing", "no",
     "ASK does not draw the plan; that is PLAN's one artifact",
     "plan_gate.py:578-590"),
    ("ask", "plan_file", "",
     "ASK writes nothing, so it has no plan artifact",
     "plan_gate.py:578-590"),
    ("ask", "switch_mode_middleware",
     "f_mode_switch.py (hotkey) + mode_attest.py (the authority)",
     "the mode is switched by the hotkey and PROVEN by mode_attest",
     "mode_skills.json:_step0; mode_attest.py:344"),
    ("ask", HELP_RIGHT,
     "Answer in text — that is the deliverable. If the answer needs code or a "
     "command, say so, then move to PLAN and write ONE file: %s (template: %s). "
     "That plan is what unlocks writing — a deny with no next step is worse "
     "than no deny." % (PLAN_ARTIFACT, PLAN_TEMPLATE),
     "a refusal must name the next act, not only refuse",
     "user 2026-09-23: 'how to give help before complain'"),
    # ---- PLAN: research + the plan artifact. Nothing else. ----
    ("plan", "terminal", "no",
     "PLAN is read-only; running a command is a write",
     "plan_gate.py:592-600 (PLAN branch denies every write)"),
    ("plan", "coding_writing", "no",
     "product code is written in AGENT, after the plan is APPROVED",
     "plan_gate.py:592-600"),
    ("plan", "plan_writing", "yes",
     "PLAN's ONE artifact is the plan; that is the whole output",
     "plan_gate.py:RIGHTS['PLAN'] 'Write exactly ONE artifact'"),
    ("plan", "plan_file", PLAN_ARTIFACT,
     "the plan lives in one machine-readable location, so the unlock can find it",
     "mode_skills.json:_unlock.artifact"),
    ("plan", "switch_mode_middleware",
     "f_mode_switch.py (hotkey) + mode_attest.py (the authority)",
     "the mode is switched by the hotkey and PROVEN by mode_attest",
     "mode_skills.json:_step0"),
    ("plan", HELP_RIGHT,
     "Write ONE file: %s (template: %s). It must carry scope, findings with "
     "real citations (path:line, or a command that ran), steps, a LOCKED QC "
     "checklist, a file allowlist and a command allowlist. Then mark it "
     "`**Status:** APPROVED` on its own line — approval is what starts AGENT."
     % (PLAN_ARTIFACT, PLAN_TEMPLATE),
     "the plan IS the deliverable, so the help is the exact path AND the marker",
     "plan_gate.py:RIGHTS['PLAN']; plan_gate.APPROVED_RE"),
    # ---- AGENT: execute the approved plan, within its allowlist. ----
    ("agent", "terminal", "yes",
     "AGENT runs the commands the APPROVED plan allowlists",
     "plan_gate.py:RIGHTS['AGENT'] 'Run commands in the plan's allowlist'"),
    ("agent", "coding_writing", "yes",
     "AGENT edits the files the APPROVED plan allowlists",
     "plan_gate.py:RIGHTS['AGENT'] 'Edit files in the plan's allowlist'"),
    ("agent", "plan_writing", "no",
     "the plan is already written and APPROVED; rewriting it would move the goal",
     "plan_gate.py:602-674 (AGENT requires an APPROVED plan)"),
    ("agent", "plan_file", PLAN_ARTIFACT,
     "the SAME path is read here: it is the unlock, not a second artifact",
     "mode_skills.json:_unlock.artifact"),
    ("agent", "switch_mode_middleware",
     "f_mode_switch.py (hotkey) + mode_attest.py (the authority)",
     "the mode is switched by the hotkey and PROVEN by mode_attest",
     "mode_skills.json:_step0"),
    ("agent", HELP_RIGHT,
     "An APPROVED plan for THIS session is the unlock. If it is missing, add a "
     "line `**Session:** <session_id>` to your plan and mark it "
     "`**Status:** APPROVED`.",
     "the unlock is per-session, so the help names the exact line to add",
     "plan_gate.py:602-674; plan_gate.SESSION_RE"),
)


class ModeRefused(RuntimeError):
    """Raised when a mode row would be stored without a real key or citation."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("mode_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the three tables and register them in `db_table_registry`."""
    import db_schema as ds

    conn.execute("PRAGMA foreign_keys = ON;")
    # 🔴 `ds.mode_registry_DDL` / `ds.mode_right_registry_DDL` DO NOT EXIST.
    # MEASURED 2026-09-29: the real names are `MODE_registry_DDL` and
    # `MODE_RIGHT_registry_DDL` (`db_schema.py`).
    for ddl in (ds.MODE_registry_DDL, ds.MODE_RIGHT_registry_DDL,
                ds.WORKER_MODE_DDL):
        conn.executescript(ddl)
    # Best-effort taxonomy rows: a throwaway DB (a proof) has no
    # `db_table_registry`, and its absence must not stop the tables existing.
    for key, desc in (
        ("mode_registry", "the modes (ask / plan / agent) as rows"),
        ("mode_right_registry", "the 6 rights x mode matrix, incl. the HELP row"),
        ("worker_mode", "which modes apply to which worker"),
    ):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO db_table_registry "
                "(table_key, name, description, is_active, version) "
                "VALUES (?, ?, ?, 1, '1')", (key, key, desc))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return {"ok": True, "tables": ["mode_registry", "mode_right_registry",
                                   "worker_mode"]}


def seed_modes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the modes and the full matrix.

    UPSERT for the RIGHTS (changed 2026-09-23). WHY: this matrix is REFERENCE
    data — a lookup table whose SSOT is the seed, like `APP_SEED`. A row that
    drifts from its seed is a DEFECT, not a local customisation. MEASURED: the
    help text was corrected in the seed but the DB kept the old text, so
    `--help-for` and the gate's deny kept emitting the superseded wording; a
    reader could not tell the two sources apart. The MODES stay INSERT OR IGNORE
    (a mode may carry a human-authored `definition`). A row for a mode or right
    that is NOT in the seed is never touched.
    """
    ensure_schema(conn)
    added_modes, added_rights, updated_rights = 0, 0, 0
    for key, name, order, cite in MODE_SEED:
        cur = conn.execute(
            "INSERT OR IGNORE INTO mode_registry "
            "(mode_key, display_name, is_system, definition, sort_order, cite_ref) "
            "VALUES (?, ?, 1, ?, ?, ?)",
            (key, name, "the %s mode" % key, order, cite))
        added_modes += int(cur.rowcount or 0)
    for i, (mk, rk, val, why, cite) in enumerate(MODE_RIGHT_SEED, 1):
        row = conn.execute(
            "SELECT value_text, why, cite_ref FROM mode_right_registry "
            "WHERE mode_key = ? AND right_key = ?", (mk, rk)).fetchone()
        cur = conn.execute(
            "INSERT INTO mode_right_registry "
            "(mode_key, mode_id, right_key, value_text, why, cite_ref, sort_order) "
            "VALUES (?, (SELECT mode_id FROM mode_registry WHERE mode_key=?), ?, ?, ?, ?, ?) "
            "ON CONFLICT(mode_key, right_key) DO UPDATE SET "
            "value_text = excluded.value_text, why = excluded.why, "
            "cite_ref = excluded.cite_ref, sort_order = excluded.sort_order",
            (mk, mk, rk, val, why, cite, i))
        if row is None:
            added_rights += 1
        elif (row[0], row[1], row[2]) != (val, why, cite):
            updated_rights += 1
    conn.commit()
    return {"ok": True, "modes_added": added_modes, "rights_added": added_rights,
            "rights_updated": updated_rights,
            "modes_total": conn.execute(
                "SELECT COUNT(*) FROM mode_registry").fetchone()[0],
            "rights_total": conn.execute(
                "SELECT COUNT(*) FROM mode_right_registry").fetchone()[0]}


def list_modes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every active mode, in declared order."""
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM mode_registry WHERE is_active = 1 "
        "ORDER BY sort_order, mode_key")]
    return {"ok": True, "modes": rows, "count": len(rows)}


def rights_for(conn: sqlite3.Connection, mode_key: str) -> dict[str, Any]:
    """ONE mode's full right map. A MISSING right is REPORTED, never invented.

    Returns every one of the SIX right keys, ALWAYS. A right with no row is
    `present: False` — because a gap must be visible, and `redirect()` treats it
    as a FAULT rather than falling back to prose.
    """
    ensure_schema(conn)
    key = str(mode_key or "").strip().lower()
    if not key:
        return {"ok": False, "error": "mode_key is required"}
    exists = conn.execute(
        "SELECT 1 FROM mode_registry WHERE mode_key = ? AND is_active = 1",
        (key,)).fetchone()
    if not exists:
        return {"ok": False,
                "error": "unknown mode %r — a guessed mode is not acceptable"
                         % mode_key}
    have = {str(r["right_key"]): dict(r) for r in conn.execute(
        "SELECT * FROM mode_right_registry WHERE mode_key = ? AND is_active = 1",
        (key,))}
    rights = []
    for rk in RIGHT_KEYS:
        r = have.get(rk)
        rights.append({
            "right_key": rk,
            "present": r is not None,
            "value_text": str(r["value_text"]) if r else "",
            "why": str(r["why"]) if r else "",
            "cite_ref": str(r["cite_ref"]) if r else "",
        })
    return {"ok": True, "mode_key": key, "rights": rights,
            "present_count": sum(1 for x in rights if x["present"]),
            "total": len(RIGHT_KEYS),
            "complete": all(x["present"] for x in rights)}


def all_rights(conn: sqlite3.Connection) -> dict[str, Any]:
    """The whole matrix, as mode -> {right_key: value} plus the metadata."""
    ensure_schema(conn)
    out: dict[str, Any] = {}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM mode_right_registry WHERE is_active = 1 "
        "ORDER BY sort_order, mode_key")]
    for r in rows:
        out.setdefault(str(r["mode_key"]), {})[str(r["right_key"])] = {
            "value_text": str(r["value_text"]), "why": str(r["why"]),
            "cite_ref": str(r["cite_ref"]),
        }
    return {"ok": True, "matrix": out, "right_keys": list(RIGHT_KEYS),
            "modes": sorted(out.keys())}


def help_for(conn: sqlite3.Connection, mode_key: str) -> str:
    """THE HELP for a mode: the exact next act. '' when the row is missing.

    Used by `redirect()` so EVERY deny carries the way forward. An empty string
    is a FAULT SIGNAL, not a quiet default — a deny with no help is a defect.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT value_text FROM mode_right_registry WHERE mode_key = ? AND "
        "right_key = ? AND is_active = 1",
        (str(mode_key or "").strip().lower(), HELP_RIGHT)).fetchone()
    return str(row["value_text"]) if row else ""


def set_worker_mode(conn: sqlite3.Connection, *, worker_key: str, mode_key: str,
                    is_applied: bool = True, why: str = "NA",
                    cite_ref: str = "") -> dict[str, Any]:
    """Answer "mode is apply for this worker or not", as a ROW.

    A worker must exist and the mode must be registered, so neither a phantom
    worker nor a guessed mode can be stored.
    """
    import worker_registry as wr

    reasons: list[str] = []
    mk = str(mode_key or "").strip().lower()
    if mk not in [m[0] for m in MODE_SEED] and not conn.execute(
            "SELECT 1 FROM mode_registry WHERE mode_key = ?", (mk,)).fetchone():
        reasons.append("mode_key %r is not a registered mode" % mode_key)
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no finding)")
    if reasons:
        raise ModeRefused(reasons)

    ensure_schema(conn)
    wr.ensure_schema(conn)
    w = conn.execute("SELECT worker_id FROM worker_registry WHERE worker_key = ?",
                     (str(worker_key or "").strip(),)).fetchone()
    if not w:
        raise ModeRefused(["worker_key %r is not registered" % worker_key])

    wid = int(w["worker_id"])
    conn.execute(
        "INSERT INTO worker_mode (worker_id, mode_key, mode_id, is_applied, why, cite_ref) "
        "VALUES (?, ?, (SELECT mode_id FROM mode_registry WHERE mode_key=?), ?, ?, ?) "
        "ON CONFLICT(worker_id, mode_key) DO UPDATE SET "
        "is_applied=excluded.is_applied, why=excluded.why, "
        "cite_ref=excluded.cite_ref, updated_at=datetime('now')",
        (wid, mk, mk, 1 if is_applied else 0, str(why or "NA"),
         str(cite_ref).strip()))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM worker_mode WHERE worker_id = ? AND mode_key = ?",
        (wid, mk)).fetchone()
    return {"ok": True, "worker_mode": dict(row)}


# ---------------------------------------------------------------------------
# THE WORKER-TYPE RULE. Why a mode applies to a worker, as ONE table.
# ---------------------------------------------------------------------------
# A mode is not applied by taste. A worker whose declared `worker_type` CHANGES
# what it does (a code function, an http API, a vision model) acts in AGENT: it
# RUNS and it EDITS. A worker whose type is `openclaw_gui`, or one declared to be
# only a GUI SURFACE, has the edge of a GUI: it does not run shell commands for
# the worker, so AGENT does not apply to it. This is the ONLY reason in the seed,
# so the whole column is reproducible from `worker_type` + this one rule.
#
# `cite_ref` is not decoration: each row points at the row it was DERIVED from,
# so a reader can check the column by hand instead of trusting the seed.
AGENT_WORKER_TYPES: tuple[str, ...] = (
    "code_function",
    "http_api",
    "vision_llm",
)
GUI_SURFACE_MARKERS: tuple[str, ...] = (
    "gui",
    "companion / screenshot",
)


def seed_worker_modes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Derive `worker_mode` rows from `worker_type`. Idempotent.

    Reads `worker_registry` and writes ONE row per (worker, mode) for the three
    registered modes. It never invents a worker and never guesses a mode: both
    must already exist, which is why `set_worker_mode` refuses a phantom.
    """
    ensure_schema(conn)
    import worker_registry as wr

    wr.ensure_schema(conn)
    workers = [dict(r) for r in conn.execute(
        "SELECT worker_id, worker_key, worker_type, uses_text "
        "FROM worker_registry WHERE is_active = 1 ORDER BY worker_key")]
    if not workers:
        return {"ok": False, "error": "no active worker in worker_registry; "
                                      "seed workers first"}

    written: list[dict[str, Any]] = []
    for w in workers:
        wkey = str(w["worker_key"])
        wtype = str(w.get("worker_type") or "")
        ut = str(w.get("uses_text") or "").lower()
        is_surface = any(m in ut for m in GUI_SURFACE_MARKERS) or \
            wtype == "openclaw_gui"
        agent_applies = (wtype in AGENT_WORKER_TYPES) and not is_surface
        cite_self = "worker_registry.worker_key=%s" % wkey
        for mode_key, why, applied in (
            ("ask",
             "every worker may research and answer in text",
             True),
            ("plan",
             "every worker may carry a plan; the gate reads it, not the mode name",
             True),
            ("agent",
             "agent applies iff worker_type %r acts (runs/edits), not a GUI "
             "surface; uses_text=%r" % (wtype, w.get("uses_text")),
             agent_applies),
        ):
            row = set_worker_mode(
                conn, worker_key=wkey, mode_key=mode_key, is_applied=applied,
                why=why, cite_ref="%s; mode_registry.AGENT_WORKER_TYPES" % cite_self)
            written.append({"worker_key": wkey, "mode_key": mode_key,
                            "is_applied": applied})
    return {"ok": True, "workers": len(workers), "rows": len(written),
            "agent_applied_to": [r["worker_key"] for r in written
                                 if r["mode_key"] == "agent" and r["is_applied"]],
            "written": written}


def modes_for_worker(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """Which modes apply to a worker. ABSENT rows are returned as `is_applied: 0`
    so the answer is complete and a human can see what is not applied."""
    ensure_schema(conn)
    w = conn.execute("SELECT worker_id, worker_key FROM worker_registry WHERE "
                     "worker_key = ?",
                     (str(worker_key or "").strip(),)).fetchone()
    if not w:
        return {"ok": False, "error": "worker_key %r is not registered"
                                      % worker_key}
    wid = int(w["worker_id"])
    applied = {str(r["mode_key"]): dict(r) for r in conn.execute(
        "SELECT * FROM worker_mode WHERE worker_id = ?", (wid,))}
    modes = []
    for mk, name, order, _cite in MODE_SEED:
        r = applied.get(mk)
        modes.append({
            "mode_key": mk, "display_name": name,
            "is_applied": bool(int(r["is_applied"])) if r else False,
            "why": str(r["why"]) if r else "",
            "has_row": r is not None,
        })
    return {"ok": True, "worker_key": str(w["worker_key"]), "modes": modes,
            "applied": [m["mode_key"] for m in modes if m["is_applied"]]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--seed-worker-modes", action="store_true")
    ap.add_argument("--modes", action="store_true")
    ap.add_argument("--rights", action="store_true")
    ap.add_argument("--mode", default="")
    ap.add_argument("--help-for", default="")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_modes(conn), ensure_ascii=False, indent=2))
            return 0
        if args.seed_worker_modes:
            print(json.dumps(seed_worker_modes(conn), ensure_ascii=False,
                             indent=2))
            return 0
        if args.modes:
            print(json.dumps(list_modes(conn), ensure_ascii=False, indent=2))
            return 0
        if args.rights:
            out = (rights_for(conn, args.mode) if args.mode
                   else all_rights(conn))
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.help_for:
            print(help_for(conn, args.help_for))
            return 0
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
