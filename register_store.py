"""Registers: skill / study / prompt / workflow.

The formula this module implements:

    prompt   = skill + study + wording
    workflow = ordered sequence of INDEPENDENT prompts

Before this, only `format_templates` (coords.db) existed and it conflated all
three axes into one row. These registers split them apart so a prompt can be
REPRODUCED from its parts instead of copied.

Pattern follows `init_ontology_registry.sql` exactly:
  {x}_id PK, {x}_key UNIQUE, name, description, {parent}_id FK,
  is_active CHECK(0,1), version, timestamps.

Laws:
  - SOFT DELETE ONLY. `is_active = 0`, never DELETE. History must survive.
  - Validators READ only. Writes are human-approved.

CROSS-DB: `prompt_registry.template_id` points at `format_templates.id`, which
lives in **coords.db**, not agent.db. SQLite cannot enforce a FK across files,
so it is a DOCUMENTED cross-DB reference, NOT a FOREIGN KEY.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

REGISTER_TABLES = (
    "component_registry",
    "skill_registry",
    "study_registry",
    "wording_registry",
    "prompt_registry",
    "prompt_wording",
    "workflow_registry",
    "workflow_step",
)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    # A TIMEOUT, because MEASURED (2026-09-25): `register_fill.py --fill-all
    # --apply` holds a long write transaction, and a bare `sqlite3.connect()`
    # waits 5 s and then raises `database is locked`. A caller that cannot wait
    # is a caller that cannot write, and the failure looked like a code defect
    # rather than a busy DB.
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_registers(conn: sqlite3.Connection) -> None:
    """Create the register tables if missing (safe to call repeatedly)."""
    # 🔴 FOUR OF THESE NAMES DID NOT EXIST. MEASURED 2026-09-29: the real names
    # are `COMPONENT_registry_DDL`, `PROMPT_registry_DDL`, `SKILL_registry_DDL`
    # and `WORKFLOW_registry_DDL` (`db_schema.py`). The lowercase spellings
    # appear NOWHERE in `db_schema.py`, so this function raised `ImportError` on
    # EVERY call — and it is the register bootstrap, so every caller of
    # `ensure_registers` was silently doing nothing.
    from db_schema import (
        COMPONENT_registry_DDL,
        PROMPT_registry_DDL,
        PROMPT_WORDING_DDL,
        SKILL_registry_DDL,
        SOFT_DELETE_LOG_DDL,
        STUDY_registry_DDL,
        WORDING_registry_DDL,
        WORKFLOW_registry_DDL,
        WORKFLOW_STEP_DDL,
    )

    # component_registry_DDL FIRST: after the split, wording_registry /
    # study_registry / prompt_registry carry a FK to component_registry, so
    # creating them against a fresh database before it exists would be an
    # ordering error — and on a bare DB `CREATE TABLE IF NOT EXISTS` would not
    # complain, it would simply leave the FK dangling.
    for ddl in (
        COMPONENT_registry_DDL,
        SKILL_registry_DDL,
        SOFT_DELETE_LOG_DDL,
        STUDY_registry_DDL,
        WORDING_registry_DDL,
        PROMPT_registry_DDL,
        PROMPT_WORDING_DDL,
        WORKFLOW_registry_DDL,
        WORKFLOW_STEP_DDL,
    ):
        conn.executescript(ddl)
    conn.commit()


# ---------------------------------------------------------------------------
# components (the composition engine calls these "skills")
# ---------------------------------------------------------------------------
# POST-SPLIT (2026-09-21): this module is the PROMPT COMPOSITION engine
# (`prompt = skill + study + wording`). Its "skill" is a COMPONENT — the parent
# of `wording_registry` — NOT one of the 27 real skills. `skill_registry` and
# `component_registry` hold DISJOINT populations (measured: INTERSECT = 1 row,
# `mouse_spot_verify`), so reading `skill_registry` here silently picks up a
# table no `wording_registry` row belongs to. The composition side reads
# `component_registry`; `component_registry.skill_ref` is the BY-ID bridge for a
# component that IS a skill.
#
# The parameter name and the returned column (`skill_id`, `skill_key`) are KEPT,
# because they are the engine's own vocabulary and the FK columns still say
# `skill_id`. Renaming them is a follow-up, not part of a rename migration.
def add_skill(
    skill_key: str,
    name: str,
    *,
    description: str | None = None,
    output_schema: str | None = None,
    parser: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Insert a COMPONENT. Returns skill_id. Raises on duplicate key."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO component_registry (skill_key, name, description, "
            "output_schema, parser) VALUES (?, ?, ?, ?, ?)",
            (skill_key, name, description, output_schema, parser),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_skills(
    *, active_only: bool = True, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """The COMPONENTS (prompt composition targets). NOT the real skills."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        sql = "SELECT * FROM component_registry"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY skill_key"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def get_skill(
    skill_key: str, *, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        row = conn.execute(
            "SELECT * FROM component_registry WHERE skill_key = ? AND is_active = 1",
            (skill_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# studies
# ---------------------------------------------------------------------------
def add_study(
    study_key: str,
    name: str,
    skill_id: int,
    *,
    description: str | None = None,
    fields_json: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Insert a study. FK skill_id must resolve, else IntegrityError."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO study_registry (study_key, name, description, skill_id, fields_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (study_key, name, description, int(skill_id), fields_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_studies(
    *, active_only: bool = True, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        sql = (
            "SELECT s.*, k.skill_key FROM study_registry s "
            "JOIN component_registry k ON k.skill_id = s.skill_id"
        )
        if active_only:
            sql += " WHERE s.is_active = 1"
        sql += " ORDER BY s.study_key"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------
def add_prompt(
    prompt_key: str,
    name: str,
    skill_id: int,
    study_id: int,
    *,
    description: str | None = None,
    template_id: int | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Insert a prompt. Both FKs must resolve, else IntegrityError.

    `template_id` is a cross-DB reference to coords.db `format_templates.id`
    and is deliberately NOT a FOREIGN KEY.

    Wording is NOT a column here: a prompt picks one wording VALUE PER
    DIMENSION, stored in `prompt_wording`. Use `set_prompt_wording()`.
    """
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO prompt_registry "
            "(prompt_key, name, description, skill_id, study_id, template_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                prompt_key,
                name,
                description,
                int(skill_id),
                int(study_id),
                None if template_id is None else int(template_id),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_prompts(
    *, active_only: bool = True, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        sql = (
            "SELECT p.*, k.skill_key, s.study_key FROM prompt_registry p "
            "JOIN component_registry k ON k.skill_id = p.skill_id "
            "JOIN study_registry s ON s.study_id = p.study_id"
        )
        if active_only:
            sql += " WHERE p.is_active = 1"
        sql += " ORDER BY p.prompt_key"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def get_prompt(
    prompt_key: str, *, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        row = conn.execute(
            "SELECT * FROM prompt_registry WHERE prompt_key = ? AND is_active = 1",
            (prompt_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# wording  (the dimension values a prompt picks from)
# ---------------------------------------------------------------------------
def add_wording(
    wording_key: str,
    name: str,
    skill_id: int,
    dim_key: str,
    template: str,
    *,
    description: str | None = None,
    sort_order: int = 0,
    db_path: Path | str | None = None,
) -> int:
    """Insert one wording VALUE for one dimension of one skill."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO wording_registry "
            "(wording_key, name, description, skill_id, dim_key, template, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wording_key, name, description, int(skill_id), dim_key, template, int(sort_order)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_wording(
    skill_key: str, *, dim_key: str | None = None, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        sql = (
            "SELECT w.* FROM wording_registry w "
            "JOIN component_registry k ON k.skill_id = w.skill_id "
            "WHERE k.skill_key = ? AND w.is_active = 1"
        )
        params: list[Any] = [skill_key]
        if dim_key:
            sql += " AND w.dim_key = ?"
            params.append(dim_key)
        sql += " ORDER BY w.dim_key, w.sort_order, w.wording_key"
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def set_prompt_wording(
    prompt_id: int,
    dim_key: str,
    wording_id: int,
    *,
    db_path: Path | str | None = None,
) -> int:
    """Bind one wording value to a prompt for one dimension.

    UNIQUE(prompt_id, dim_key) means a second value for the same dimension is a
    contradiction and raises IntegrityError — that is intended.
    """
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO prompt_wording (prompt_id, wording_id, dim_key) VALUES (?, ?, ?)",
            (int(prompt_id), int(wording_id), dim_key),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_prompt_wording(
    prompt_id: int, *, db_path: Path | str | None = None
) -> dict[str, str]:
    """The prompt's chosen wording, as {dim_key: wording_key}."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        return {
            r["dim_key"]: r["wording_key"]
            for r in conn.execute(
                "SELECT pw.dim_key, w.wording_key FROM prompt_wording pw "
                "JOIN wording_registry w ON w.wording_id = pw.wording_id "
                "WHERE pw.prompt_id = ?",
                (int(prompt_id),),
            )
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------
def add_workflow(
    workflow_key: str,
    name: str,
    *,
    description: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO workflow_registry (workflow_key, name, description) VALUES (?, ?, ?)",
            (workflow_key, name, description),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def add_workflow_step(
    workflow_id: int,
    step_no: int,
    prompt_id: int,
    *,
    is_final: bool = False,
    notes: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Append one ordered step. UNIQUE(workflow_id, step_no) guards ordering."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        cur = conn.execute(
            "INSERT INTO workflow_step (workflow_id, step_no, prompt_id, is_final, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(workflow_id), int(step_no), int(prompt_id), 1 if is_final else 0, notes),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_workflows(
    *, active_only: bool = True, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        sql = "SELECT * FROM workflow_registry"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY workflow_key"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def get_workflow(
    workflow_key: str, *, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    """Return the workflow with its ordered steps, each prompt resolved."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        wf = conn.execute(
            "SELECT * FROM workflow_registry WHERE workflow_key = ? AND is_active = 1",
            (workflow_key,),
        ).fetchone()
        if not wf:
            return None
        steps = conn.execute(
            "SELECT ws.step_no, ws.is_final, ws.notes, "
            "       p.prompt_id, p.prompt_key, p.name AS prompt_name, "
            "       p.template_id, "
            "       k.skill_key, s.study_key "
            "FROM workflow_step ws "
            "JOIN prompt_registry p ON p.prompt_id = ws.prompt_id "
            "JOIN component_registry  k ON k.skill_id  = p.skill_id "
            "JOIN study_registry  s ON s.study_id  = p.study_id "
            "WHERE ws.workflow_id = ? "
            "ORDER BY ws.step_no",
            (int(wf["workflow_id"]),),
        ).fetchall()
        out = dict(wf)
        out["steps"] = [dict(r) for r in steps]
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# THE TUTORIAL WORKFLOW (2026-09-25).
#
# THE HUMAN:
#     "identity has these table already, i don't know the name , here is sample
#      called tutorial"
#     "workflow table
#        id | tutorial_id | logic generation
#        id | tutorial_id | data anaylze
#        ...
#        id | tutorial_id | Registered Verfity"
#
# MEASURED, and this is the answer to "i don't know the name": the tables ARE
# `workflow_registry` (10 rows) and `workflow_step` (114 rows), and
# `subject_kind_registry` already declares `kind_key='workflow'` ->
# `ref_table='workflow_registry'`, `ref_column='workflow_id'`.
#
# MEASURED, and this is the gap: NONE of the human's 11 steps existed
# (`logic generation` -> 0, `data analyze` -> 0, ... `registered verify` -> 0).
#
# WHY THE STEP NAMES GO IN `notes` AND THE KINDS REUSE THE EXISTING VOCABULARY:
# MEASURED, `workflow_step.step_kind` already has `describe` (93), `gate` (8),
# `verdict` (8). Inventing 11 new kinds would make every existing reader of
# `step_kind` wrong. The step NAME carries the human's wording; the KIND carries
# the machine meaning.
#
# `prompt_id` is NOT NULL in the schema, so each step is bound to the workflow's
# own prompt row. A step with no prompt cannot be run, and a NULL prompt_id would
# be a step that silently does nothing.
# ---------------------------------------------------------------------------
TUTORIAL_WORKFLOW_KEY = "tutorial"

# THE NAMED SKILL AND STUDY (added 2026-09-26). MEASURED DEFECT: the seed used
# `ORDER BY skill_id LIMIT 1`, so the tutorial prompt was bound to
# `captcha_cell_detect` (skill_id=1) — an arbitrary row. A seed that picks an
# arbitrary row cannot be measured, because the same call on a different DB
# binds a different skill. These names make the binding DECLARED.
TUTORIAL_SKILL_KEY = "tutorial"
TUTORIAL_STUDY_KEY = "worker_identity_case"

# THE TUTORIAL'S OWN CONTRACT (added 2026-09-26).
#
# MEASURED: the tutorial flow's 11 steps are `logic generation` -> ... ->
# `registered verify`, and steps 5-11 are the FACTOR steps (`factor checklist`,
# `factor check`, `factor confirm`, `factor proof`, `factor verify`,
# `factor register`, `registered verify`). So the flow's subject is a FACTOR,
# and `factor_template` ALREADY declares what a factor must carry — 9 required
# fields, each with a `why`.
#
# MEASURED: `skill_contract_template` had 0 tutorial/factor rows, so the flow's
# final step fell back to the PHONE. This contract makes the final step
# MEASURABLE instead of hollow.
TUTORIAL_CONTRACT_ID = "TUTORIAL.FACTOR"
TUTORIAL_TAXONOMY_PATH = "module/task_center"
TUTORIAL_PURPOSE = (
    "Prove that a FACTOR was registered: every required factor field is present "
    "and non-blank, so the factor can be scored, cited and proved."
)
TUTORIAL_NOT_RESPONSIBLE = (
    "NOT responsible for judging whether the factor's metric_target is the RIGHT "
    "target, nor whether the factor is useful — only that it is COMPLETE."
)

# (step_no, name, step_kind, layer_key, step_status)
TUTORIAL_STEPS: tuple[tuple[int, str, str, str, str], ...] = (
    (1, "logic generation", "describe", "tdd", "researching"),
    (2, "data analyze", "describe", "tdd", "researching"),
    (3, "data received", "describe", "tdd", "researching"),
    (4, "data collect", "describe", "tdd", "researching"),
    (5, "factor checklist", "gate", "ontology", "writing"),
    (6, "factor check", "gate", "ontology", "writing"),
    (7, "factor confirm", "verdict", "ontology", "writing"),
    (8, "factor proof", "verdict", "tdd", "verifying"),
    (9, "factor verify", "verdict", "tdd", "verifying"),
    (10, "factor register", "describe", "tdd", "verifying"),
    (11, "registered verify", "verdict", "tdd", "verifying"),
)


def _seed_tutorial_contract(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the tutorial flow's OWN contract, DERIVED from `factor_template`.

    MEASURED (2026-09-26): the tutorial flow's final step fell back to the PHONE
    because `skill_contract_template` had 0 tutorial/factor rows. The flow's
    steps 5-11 are the FACTOR steps, so the flow's subject is a FACTOR — and
    `factor_template` ALREADY declares what a factor must carry (9 required
    fields, each with a `why`).

    The fields are DERIVED from `factor_template`, not typed here: a typed list
    would drift the moment a factor field is added. `is_required=1` becomes
    `mandatory=1`, so the contract's required-field count IS the threshold.

    Idempotent: a second call updates the same contract and re-derives the
    fields. Returns `{ok, contract_id, fields, required}` or a refusal with a
    code — never a silent partial write.
    """
    import skill_contract_store as scs

    rows = list(conn.execute(
        "SELECT field_name, kind, is_required, why FROM factor_template "
        "WHERE is_active=1 ORDER BY sort_order"))
    if not rows:
        return {"ok": False, "error_code": "NO_FACTOR_TEMPLATE",
                "error": ("factor_template has no active rows, so the tutorial "
                          "contract's fields cannot be DERIVED. Inventing them "
                          "would be fabricating a rule.")}

    r = scs.upsert_contract(
        TUTORIAL_CONTRACT_ID, TUTORIAL_SKILL_KEY, TUTORIAL_TAXONOMY_PATH,
        TUTORIAL_PURPOSE,
        purpose_not_responsible=TUTORIAL_NOT_RESPONSIBLE,
        status="active", source="tutorial_seed", conn=conn)
    if not r.get("ok"):
        return {"ok": False, "error_code": "CONTRACT_REFUSED",
                "error": "upsert_contract refused: %s" % r.get("message"),
                "detail": r}

    n_req = 0
    for row in rows:
        mand = bool(int(row["is_required"] or 0))
        if mand:
            n_req += 1
        fr = scs.upsert_field(
            TUTORIAL_CONTRACT_ID, str(row["field_name"]),
            str(row["kind"] or "text"),
            "required by factor_template" if mand else "optional",
            mandatory=mand, why=str(row["why"] or "NA"), conn=conn)
        if not fr.get("ok"):
            return {"ok": False, "error_code": "FIELD_REFUSED",
                    "error": ("upsert_field refused for %r: %s"
                              % (row["field_name"], fr.get("message"))),
                    "detail": fr}

    # THE PROMPT'S DECLARATION. Without this the contract exists but the prompt
    # still falls back — the exact defect this task repairs.
    conn.execute(
        "UPDATE prompt_registry SET oracle_ref=?, threshold=?, "
        "updated_at=datetime('now') WHERE prompt_key=?",
        (TUTORIAL_CONTRACT_ID, str(n_req), TUTORIAL_WORKFLOW_KEY))
    return {"ok": True, "contract_id": TUTORIAL_CONTRACT_ID,
            "fields": len(rows), "required": n_req}


def seed_tutorial_workflow(
    *, db_path: Path | str | None = None
) -> dict[str, Any]:
    """Create the `tutorial` workflow with its 11 steps. IDEMPOTENT.

    Returns `{ok, workflow_id, steps_written, steps_total, created}`. A second
    call writes nothing and reports `created=False`, so a re-run cannot duplicate
    the steps (UNIQUE(workflow_id, step_no) would refuse anyway, and a refusal is
    not a success).
    """
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        wf = conn.execute(
            "SELECT workflow_id FROM workflow_registry WHERE workflow_key = ?",
            (TUTORIAL_WORKFLOW_KEY,),
        ).fetchone()
        created = False
        if wf:
            workflow_id = int(wf["workflow_id"])
        else:
            cur = conn.execute(
                "INSERT INTO workflow_registry (workflow_key, name, description, is_active) "
                "VALUES (?, ?, ?, 1)",
                (
                    TUTORIAL_WORKFLOW_KEY,
                    "tutorial",
                    "The 11-step tutorial flow: logic generation -> registered verify. "
                    "MEASURED from the human's own list (2026-09-25).",
                ),
            )
            workflow_id = int(cur.lastrowid)
            created = True

        # A prompt row to bind the steps to. `prompt_id` is NOT NULL, and a step
        # with no prompt cannot be run.
        #
        # THE SKILL IS MINTED FIRST (added 2026-09-26). MEASURED: the old seed
        # bound the prompt to `ORDER BY skill_id LIMIT 1`, so it claimed
        # `captcha_cell_detect`. The skill is now NAMED and minted here, so the
        # binding is DECLARED rather than accidental.
        import skill_registrar as _sr
        minted = _sr.ensure_skill_identity(
            conn, skill_key=TUTORIAL_SKILL_KEY, name="tutorial",
            description=("The 11-step tutorial flow: logic generation -> "
                         "registered verify."),
            taxonomy_path=TUTORIAL_TAXONOMY_PATH, commit=False)
        if not minted.get("ok"):
            return {"ok": False, "error_code": "SKILL_MINT_REFUSED",
                    "error": ("ensure_skill_identity refused for %r: %s"
                              % (TUTORIAL_SKILL_KEY, minted.get("message"))),
                    "detail": minted}

        p = conn.execute(
            "SELECT prompt_id FROM prompt_registry WHERE prompt_key = ?",
            (TUTORIAL_WORKFLOW_KEY,),
        ).fetchone()
        if p:
            prompt_id = int(p["prompt_id"])
            # THE PROMPT'S SKILL IS ALSO CORRECTED (added 2026-09-26). MEASURED:
            # the existing `tutorial` prompt had `skill_id=1` =
            # `captcha_cell_detect`, because the OLD seed bound it to
            # `ORDER BY skill_id LIMIT 1`. Fixing only the INSERT branch would
            # leave the live row wrong, so the binding is corrected here too.
            skill = conn.execute(
                "SELECT skill_id FROM skill_registry WHERE skill_key = ?",
                (TUTORIAL_SKILL_KEY,)).fetchone()
            if not skill:
                return {"ok": False, "error_code": "NO_TUTORIAL_SKILL",
                        "error": ("skill_registry has no row for skill_key=%r, "
                                  "so the tutorial prompt cannot be bound to a "
                                  "NAMED skill." % TUTORIAL_SKILL_KEY)}
            conn.execute(
                "UPDATE prompt_registry SET skill_id=?, updated_at=datetime('now') "
                "WHERE prompt_id=?",
                (int(skill["skill_id"]), prompt_id))
        else:
            # THE SKILL IS NAMED, NOT "WHATEVER IS FIRST" (fixed 2026-09-26).
            #
            # MEASURED DEFECT: this used
            #     SELECT skill_id FROM component_registry ORDER BY skill_id LIMIT 1
            # so the tutorial prompt was bound to whatever skill happened to be
            # first. MEASURED: `prompt_registry.tutorial.skill_id = 1` =
            # `captcha_cell_detect` — an ARBITRARY skill, and the 11-step flow
            # therefore claimed to be about captcha cells.
            #
            # A seed that picks an arbitrary row is a seed that cannot be
            # measured: the same call on a different DB binds a different skill.
            # The skill is now NAMED, and its absence is a REFUSAL with a code —
            # never a silent bind to the wrong row.
            skill = conn.execute(
                "SELECT skill_id FROM skill_registry WHERE skill_key = ?",
                (TUTORIAL_SKILL_KEY,)).fetchone()
            if not skill:
                return {"ok": False, "error_code": "NO_TUTORIAL_SKILL",
                        "error": ("skill_registry has no row for skill_key=%r, "
                                  "so the tutorial prompt cannot be bound to a "
                                  "NAMED skill. Binding to an arbitrary row "
                                  "would make the flow unmeasurable."
                                  % TUTORIAL_SKILL_KEY)}
            study = conn.execute(
                "SELECT study_id FROM study_registry WHERE study_key = ?",
                (TUTORIAL_STUDY_KEY,)).fetchone()
            if not study:
                return {"ok": False, "error_code": "NO_TUTORIAL_STUDY",
                        "error": ("study_registry has no row for study_key=%r, "
                                  "so the tutorial prompt cannot be bound to a "
                                  "NAMED study." % TUTORIAL_STUDY_KEY)}
            cur = conn.execute(
                "INSERT INTO prompt_registry (prompt_key, name, skill_id, study_id) "
                "VALUES (?, ?, ?, ?)",
                (TUTORIAL_WORKFLOW_KEY, "tutorial", int(skill["skill_id"]),
                 int(study["study_id"])),
            )
            prompt_id = int(cur.lastrowid)

        # THE TUTORIAL'S OWN CONTRACT (added 2026-09-26). MEASURED: the flow's
        # final step fell back to the PHONE because no contract existed for it.
        # The contract is seeded from `factor_template`, which ALREADY declares
        # what a factor must carry — so the fields are DERIVED, not invented.
        contract = _seed_tutorial_contract(conn)
        if not contract.get("ok"):
            return contract

        written = 0
        for step_no, name, kind, layer, status in TUTORIAL_STEPS:
            cur = conn.execute(
                "INSERT OR IGNORE INTO workflow_step "
                "(workflow_id, step_no, prompt_id, is_final, notes, layer_key, "
                " step_kind, step_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (workflow_id, int(step_no), prompt_id,
                 1 if step_no == len(TUTORIAL_STEPS) else 0,
                 name, layer, kind, status),
            )
            written += cur.rowcount or 0
        conn.commit()
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "workflow_key": TUTORIAL_WORKFLOW_KEY,
            "created": created,
            "steps_written": written,
            "steps_total": len(TUTORIAL_STEPS),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# soft delete (the ONLY delete)
# ---------------------------------------------------------------------------
# DEFECT FOUND BY MEASURING IT (2026-09-21): this function set `is_active = 0`
# and recorded NOTHING. Measured on the one real soft delete in this repo
# (`skill_registry.skill_key = 'mcp-tool-checklist'`): the reason lived only in
# the `description` PROSE, there was no `cite_ref` column anywhere, and no
# orphan check existed. The row was disabled and NOBODY could check WHY.
#
# The user's rule: "a soft delete with no cite_ref is 'I think it is a
# duplicate', not a finding". So `cite_ref` is now REQUIRED, and it is checked
# by `citation_discipline.is_citation` — the same gate `skill_lesson` uses. An
# uncited soft delete is REFUSED, never downgraded to a low-confidence delete.
#
# The event is appended to `soft_delete_log`, which is APPEND-ONLY: a row can be
# soft-deleted, revived, and soft-deleted again, and all three events survive.
# A trace that can be rewritten is not a trace.
def soft_delete(table: str, id_column: str, row_id: int, *,
                cite_ref: str | None = None,
                reason: str | None = None,
                actor: str | None = None,
                allow_orphans: bool = False,
                db_path: Path | str | None = None) -> bool:
    """Set is_active = 0. Never DELETE. Returns True if a row changed.

    `cite_ref` is REQUIRED and must pass `citation_discipline.is_citation`
    (`path:line`, a command, an evidence id, or `register:table:pk`). Without it
    the call raises `UncitedFinding` and NOTHING is written.

    `allow_orphans=False` (the default) refuses the delete when another table
    still references the row, because a disabled row that is still referenced is
    a leak, not a soft delete. Pass `allow_orphans=True` only with a reason.
    """
    if table not in REGISTER_TABLES:
        raise ValueError(f"not a register table: {table}")
    if table == "workflow_step":
        raise ValueError("workflow_step has no is_active; delete the workflow instead")

    import citation_discipline as cd
    if not cd.is_citation(cite_ref or ""):
        raise cd.UncitedFinding(
            "uncited soft delete — refusing to disable %s.%s=%s: %r is not a "
            "checkable citation — need path:line, a command, or "
            "register:table:pk" % (table, id_column, row_id, cite_ref))

    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ?", (int(row_id),)
        ).fetchone()
        if row is None:
            return False
        row_key = None
        for k in ("skill_key", "component_key", "study_key", "wording_key",
                  "prompt_key", "workflow_key", "name"):
            if k in row.keys():
                row_key = row[k]
                break

        # THE ORPHAN CHECK. A soft delete that leaves live references behind is
        # a leak: the referencing row still points at something the system now
        # treats as gone. Measured 2026-09-21: the one real soft delete had 0
        # orphans, so this check passes on real data — it is a guard, not a
        # repair.
        orphans = _count_orphans(conn, table, row_key) if row_key else 0
        if orphans and not allow_orphans:
            raise ValueError(
                "refusing to soft-delete %s.%s=%s (%s): %d row(s) in other "
                "tables still reference it. A disabled row that is still "
                "referenced is a leak, not a soft delete. Pass "
                "allow_orphans=True with a reason to override."
                % (table, id_column, row_id, row_key, orphans))

        cur = conn.execute(
            f"UPDATE {table} SET is_active = 0, updated_at = datetime('now') "
            f"WHERE {id_column} = ? AND is_active = 1",
            (int(row_id),),
        )
        if cur.rowcount > 0:
            conn.execute(
                "INSERT INTO soft_delete_log (table_name, id_column, row_id, "
                "row_key, cite_ref, reason, actor, orphan_refs) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (table, id_column, int(row_id), row_key, cite_ref, reason,
                 actor, orphans))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _count_orphans(conn: sqlite3.Connection, table: str, row_key: str) -> int:
    """Count rows in OTHER tables that still reference `row_key` by skill_key.

    Only `skill_key` is checked, because that is the column the repo actually
    uses to cross-reference a skill. A table that references by numeric id is
    NOT covered here, and that limit is stated rather than hidden.
    """
    total = 0
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE "
        "'%skill_key%' ORDER BY name")]
    for t in tabs:
        if t in (table, "soft_delete_log"):
            continue
        try:
            total += conn.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE skill_key = ?',
                (row_key,)).fetchone()[0]
        except sqlite3.Error:
            continue
    return total


def soft_delete_history(table: str, row_id: int, *,
                        db_path: Path | str | None = None) -> list[dict]:
    """Every soft-delete event for a row, oldest first. Append-only."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM soft_delete_log WHERE table_name=? AND row_id=? "
            "ORDER BY id", (table, int(row_id)))]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# seed from MEASURED data
# ---------------------------------------------------------------------------
# The 4 output-format rows in coords.db `format_templates` are skills: each is a
# capability plus an output contract.
SEED_SKILLS = (
    ("yes_no", "Yes/No verdict", "Binary YES/NO verdict output contract", "Result: [YES / NO]", "result_yes_no"),
    ("tdd_verify", "TDD verify", "TDD verification output contract", None, "result_yes_no"),
    ("verdict_3line", "3-line verdict", "Fixed 3-line verdict output contract", None, "verdict_3line"),
    ("test_json_verdict", "JSON verdict", "JSON verdict output contract", None, "result_json"),
)

# The identity case study: the fields both identity prompts echo back.
SEED_STUDY_FIELDS = [
    {"sort": 1, "field": "SESSION_ID"},
    {"sort": 2, "field": "MODEL"},
    {"sort": 3, "field": "CHAT_ID"},
    {"sort": 4, "field": "CHAT_SHA256"},
]


def seed_registers(db_path: Path | str | None = None) -> dict[str, Any]:
    """Seed the registers from the measured `format_templates` rows.

    Idempotent: existing keys are left alone.

    ASSUMPTION (flagged, one-line to change): the `worker_identity` study is
    attached to the `verdict_3line` skill, because the confirm prompt's output
    is a table. If the identity prompts belong to a different skill, change
    `study_skill_key` below.
    """
    study_skill_key = "verdict_3line"

    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        created: dict[str, list[str]] = {"skills": [], "studies": [], "prompts": [], "workflows": [], "steps": []}

        # 1. components (the composition engine's "skills")
        for key, name, desc, schema, parser in SEED_SKILLS:
            if conn.execute("SELECT 1 FROM component_registry WHERE skill_key = ?", (key,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO component_registry (skill_key, name, description, output_schema, parser) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, name, desc, schema, parser),
            )
            created["skills"].append(key)

        skill_id = conn.execute(
            "SELECT skill_id FROM component_registry WHERE skill_key = ?", (study_skill_key,)
        ).fetchone()
        if not skill_id:
            raise RuntimeError(f"seed skill missing: {study_skill_key}")
        skill_id = int(skill_id["skill_id"])

        # 2. study
        if not conn.execute(
            "SELECT 1 FROM study_registry WHERE study_key = ?", ("worker_identity_case",)
        ).fetchone():
            conn.execute(
                "INSERT INTO study_registry (study_key, name, description, skill_id, fields_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "worker_identity_case",
                    "Worker identity case",
                    "The identity fields a worker must echo back",
                    skill_id,
                    json.dumps(SEED_STUDY_FIELDS),
                ),
            )
            created["studies"].append("worker_identity_case")

        study_id = int(
            conn.execute(
                "SELECT study_id FROM study_registry WHERE study_key = ?",
                ("worker_identity_case",),
            ).fetchone()["study_id"]
        )

        # 3. prompts — BOTH identity rows are prompts (user-confirmed)
        seed_prompts = (
            ("worker_identity", "Worker identity paste", 2609),
            ("worker_identity_confirm", "Worker identity confirm", 4481),
        )
        for key, name, template_id in seed_prompts:
            if conn.execute("SELECT 1 FROM prompt_registry WHERE prompt_key = ?", (key,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO prompt_registry "
                "(prompt_key, name, description, skill_id, study_id, template_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, name, None, skill_id, study_id, template_id),
            )
            created["prompts"].append(key)

        # 4. workflow + steps
        if not conn.execute(
            "SELECT 1 FROM workflow_registry WHERE workflow_key = ?", ("worker_identity",)
        ).fetchone():
            conn.execute(
                "INSERT INTO workflow_registry (workflow_key, name, description) VALUES (?, ?, ?)",
                ("worker_identity", "Worker identity", "Paste identity, then confirm it echoed back"),
            )
            created["workflows"].append("worker_identity")

        workflow_id = int(
            conn.execute(
                "SELECT workflow_id FROM workflow_registry WHERE workflow_key = ?",
                ("worker_identity",),
            ).fetchone()["workflow_id"]
        )

        step_plan = (
            (1, "worker_identity", False),
            (2, "worker_identity_confirm", True),
        )
        for step_no, prompt_key, is_final in step_plan:
            exists = conn.execute(
                "SELECT 1 FROM workflow_step WHERE workflow_id = ? AND step_no = ?",
                (workflow_id, step_no),
            ).fetchone()
            if exists:
                continue
            pid = conn.execute(
                "SELECT prompt_id FROM prompt_registry WHERE prompt_key = ?", (prompt_key,)
            ).fetchone()
            if not pid:
                raise RuntimeError(f"seed prompt missing: {prompt_key}")
            conn.execute(
                "INSERT INTO workflow_step (workflow_id, step_no, prompt_id, is_final) "
                "VALUES (?, ?, ?, ?)",
                (workflow_id, step_no, int(pid["prompt_id"]), 1 if is_final else 0),
            )
            created["steps"].append(f"{step_no}:{prompt_key}")

        conn.commit()
        return {"ok": True, "created": created}
    finally:
        conn.close()


def verify_no_orphans(db_path: Path | str | None = None) -> dict[str, Any]:
    """Every seeded row must resolve its parent. Returns the orphan counts."""
    conn = _connect(db_path)
    try:
        ensure_registers(conn)
        checks = {
            "study_without_skill": (
                "SELECT COUNT(*) FROM study_registry s "
                "LEFT JOIN component_registry k ON k.skill_id = s.skill_id WHERE k.skill_id IS NULL"
            ),
            "prompt_without_skill": (
                "SELECT COUNT(*) FROM prompt_registry p "
                "LEFT JOIN component_registry k ON k.skill_id = p.skill_id WHERE k.skill_id IS NULL"
            ),
            "prompt_without_study": (
                "SELECT COUNT(*) FROM prompt_registry p "
                "LEFT JOIN study_registry s ON s.study_id = p.study_id WHERE s.study_id IS NULL"
            ),
            "step_without_workflow": (
                "SELECT COUNT(*) FROM workflow_step ws "
                "LEFT JOIN workflow_registry w ON w.workflow_id = ws.workflow_id "
                "WHERE w.workflow_id IS NULL"
            ),
            "step_without_prompt": (
                "SELECT COUNT(*) FROM workflow_step ws "
                "LEFT JOIN prompt_registry p ON p.prompt_id = ws.prompt_id "
                "WHERE p.prompt_id IS NULL"
            ),
        }
        counts = {name: int(conn.execute(sql).fetchone()[0]) for name, sql in checks.items()}
        return {"ok": all(v == 0 for v in counts.values()), "orphans": counts}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="skill/study/prompt/workflow registers")
    ap.add_argument("--db", default=None)
    ap.add_argument("--seed", action="store_true", help="seed from measured data")
    ap.add_argument("--verify", action="store_true", help="check for orphans")
    ap.add_argument("--show", action="store_true", help="print the chain")
    args = ap.parse_args(argv)

    if args.seed:
        print(json.dumps(seed_registers(args.db), indent=2))
    if args.verify:
        print(json.dumps(verify_no_orphans(args.db), indent=2))
    if args.show:
        print(json.dumps(
            {
                "skills": list_skills(db_path=args.db),
                "studies": list_studies(db_path=args.db),
                "prompts": list_prompts(db_path=args.db),
                "workflows": list_workflows(db_path=args.db),
                "worker_identity": get_workflow("worker_identity", db_path=args.db),
            },
            indent=2,
            default=str,
        ))
    if not (args.seed or args.verify or args.show):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
