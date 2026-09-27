# -*- coding: utf-8 -*-
"""activation_gate.py — the ONE writer of `is_active = 1`.

WHY THIS EXISTS
---------------
`is_active` is declared `INTEGER NOT NULL DEFAULT 1` in **54 places** in
`db_schema.py`. So every row that was ever inserted is ACTIVE, and the `1` is a
DEFAULT, not a MEASUREMENT. The user's rule (2026-09-22):

    "is_active = 0 /1, default registered = 0 / how to be 1 , by 100 run proofed
    continue pass for specify times"

and

    "problem is this pieces is missing in the past, now we can have that so need
    to update the old data from 1 to 0, to have the fully test and proofed by
    LLM 7B"

So this module is the gate that turns a 100-run streak into an activation.

`is_active` IS A **VERSION** FLAG (Q6)
--------------------------------------
    "Q6 is related to version / as all the task update by new version, you can
    trace it easy and once version is proofed / can have cleanup table to
    prevent new / old version by is_active too"

Every task updates by a NEW VERSION, so the thing that gets proven is a VERSION,
not a row. `activate()` therefore takes a `version` and flips THAT version's row
in `version_register`, leaving the entity's other versions alone.

THE TWO MEANINGS OF `is_active`, AND WHY THEY ARE NOT MERGED
------------------------------------------------------------
`db_schema.py:1653` states the existing law:

    "Laws: SOFT DELETE ONLY (is_active=0, never DELETE)"

So `is_active=0` ALREADY means "retired". The user now wants it to mean "not yet
proven". One column, two meanings — the same defect family as `catalog_id`
having two readers. This module does NOT resolve that by guessing; it:

  * writes `is_active=1` ONLY through `activate()`, and
  * records the SUPERSESSION in `version_cleanup`, so "old version" is a FACT
    with a citation rather than an inference from a `0`.

WHAT IT REFUSES
---------------
  * activating below the target streak
  * activating without a citation (`citation_discipline.assert_cited`)
  * activating a version that does not exist
  * touching a table on the EXEMPT list (a blanket flip would break entity id
    resolution: `entity_id.require` needs an ACTIVE `version_register` row)

Run:
    .\\.venv\\Scripts\\python.exe activation_gate.py --status
    .\\.venv\\Scripts\\python.exe activation_gate.py --scope
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

# The default target. NOT a magic number: it is the same 100 the user named, and
# `llm_100_run_harness.STREAK_TARGET` is 110 (22 options x 5). A caller passes
# the target explicitly; this is only the fallback.
DEFAULT_TARGET = 100

# The route whose provider pool serves the 100 run. Read from the registry by
# `llm_100_run_harness.resolve_model`, never hard-coded here.
JUDGE_LLM_ROUTE = "llm.text"


class ActivationRefused(RuntimeError):
    """Raised when an activation would be unproven or unaddressable."""


# ---------------------------------------------------------------------------
# THE SCOPE, and the EXEMPTIONS
# ---------------------------------------------------------------------------
# WHY A NAMED SCOPE AND NOT "every table with is_active"
# -----------------------------------------------------
# The user's rule is "this is for all table with is_active, not for this system
# only". But a BLANKET flip breaks the system: `entity_id.require`
# (`entity_id.py:137`) requires "the version must be an ACTIVE row in
# `version_register`", so setting every `version_register.is_active=0` makes
# every entity id unresolvable. The same applies to the alphabet
# (`entity_type_register`) and the base dimensions.
#
# So the scope is NAMED, and the exemptions are NAMED WITH THEIR REASON — the
# same shape `_proof_register_approval.NO_LETTER_BECAUSE` already uses, which
# also asserts every named exemption EXISTS (no stale exemption hiding a rename)
# and carries a POSITIVE CONTROL.
#
# `ACTIVATION_SCOPE` is a SUFFIX rule, not a list: a new `*_register` table is in
# scope automatically, so the rule cannot be outgrown by adding a table.
ACTIVATION_SCOPE_SUFFIXES = ("_register", "_registry")

NO_ACTIVATION_BECAUSE: dict[str, str] = {
    # META: the alphabet and the version chain themselves. Deactivating either
    # makes every entity id unaddressable.
    "entity_type_register": ("META: it IS the alphabet. `entity_id.parse` reads "
                             "it to resolve a letter, so a 0 here breaks every "
                             "id in the system"),
    "version_register": ("META: it IS the version chain. `entity_id.require` "
                         "requires an ACTIVE row here (entity_id.py:137), so a "
                         "blanket flip makes every entity id unresolvable"),
    # BASE DIMENSIONS: a task cannot be created without them, and they are not
    # "proven" by a 100-run — they are the vocabulary the run is expressed in.
    "channel": "BASE: a task cannot be created without a channel",
    "module": "BASE: a task cannot be created without a module",
    "version_center": "BASE: the task-center version chain, not a proven thing",
    "task_action_name": "BASE: the action vocabulary, not a proven thing",
    "catalog": "BASE: the Skill Library catalog, derived from the folder path",
    "subcatalog": "BASE: the Skill Library subcatalog, derived from the path",
    # A MAPPING between two things, not a thing (same reason `chat_register` has
    # no entity letter).
    "chat_register": ("MAPPING: pairs a ticket with a chat. A mapping is not "
                      "proven by a 100-run; it is a pairing"),
    # The gate's OWN tables. Activating them would be the gate proving itself.
    "register_approve": ("GATE: this IS the approval gate. A gate cannot be "
                         "activated by the gate it is"),
    "version_cleanup": "GATE: this IS the supersession record",
    "schema_migration_log": "META: the migration log, append-only",
    # RENAMED 2026-09-22: `llm_100_run` -> `proof_run`. The user: "100 run need
    # to rename", because the count is NOT fixed (`streak_target_for` derives it
    # from the prompt's `threshold`). The OLD name is now a VIEW, so it is no
    # longer a table with `is_active` — the exemption moves to the real table.
    "proof_run": ("EVIDENCE: this IS the proof-run evidence. Activating the "
                  "evidence table would be circular"),
    # VOCABULARY: a config row is not "proven by a 100-run". It is a value the
    # run is expressed IN, so a streak cannot decide it. Measured: these 15 were
    # UNCLASSIFIED on the first run of `--scope`, which is the report working —
    # an unclassified table is neither silently included nor silently skipped.
    "agent_provider": "VOCABULARY: names an agent provider, not a proven thing",
    "app": "VOCABULARY: an application key, not a proven thing",
    "source": "VOCABULARY: a source key, not a proven thing",
    "llm_model": "VOCABULARY: a model name, not a proven thing",
    "llm_service": "VOCABULARY: a service route, not a proven thing",
    "llm_route_provider": ("VOCABULARY: the provider pool for a route. It is "
                             "what the 100-run READS to pick a model, so a "
                             "streak cannot decide it"),
    "factor_template": "VOCABULARY: a factor template, not a proven thing",
    "flow_setting": "VOCABULARY: a flow setting, not a proven thing",
    "prompt_dimension": "VOCABULARY: a prompt dimension, not a proven thing",
    "prompt_dimension_state": ("VOCABULARY: a dimension state, not a proven "
                               "thing"),
    # A CONTAINER, and one that already carries its OWN proof mechanism.
    "consultant_team": ("CONTAINER: a team holds members and skills; it is "
                        "addressed by `team_key`"),
    "consultant_skill": ("OWN GATE: this table already carries `proofed` + "
                         "`rating` + `source_ref`, and `mark_proofed` requires "
                         "a citation. A second gate here would be two answers "
                         "to one question"),
    # The TICKET family. A ticket is a WORK ITEM, not a proven artifact: it is
    # opened, worked, and closed. Its `is_active` is the soft-delete flag the
    # repo's own law defines ("SOFT DELETE ONLY (is_active=0, never DELETE)"),
    # and a 100-run cannot decide whether a ticket is open.
    "ticket": ("WORK ITEM: a ticket is opened and closed, not proven. Its "
               "is_active is the soft-delete flag"),
    "ticket_center": ("WORK ITEM: a service catalog entry, not a proven thing"),
    # CHANGED 2026-09-23: `ticket_module_map` was folded into `ticket_subject`
    # with `subject_kind='module'`, so the old name is no longer a table. Leaving
    # it here was a STALE EXEMPTION, which the proof catches on purpose ("no
    # stale exemption hiding a rename").
    "ticket_subject": ("MAPPING: pairs a ticket with a subject (module, chat, "
                       "workflow, task, entity, ...). A mapping is not proven by "
                       "a 100-run"),
    # ---------------------------------------------------------------------
    # 23 TABLES THAT WERE UNCLASSIFIED (reported by the proof, 2026-09-26).
    #
    # They are classified here on their SHAPE, in the categories this dict
    # ALREADY uses. The evidence that they are NOT activatable is MEASURED, not
    # asserted: the proof's writer scan (SECTION G) finds `is_active=1` written
    # only by `channel_registry`, `dimension_binding_register`,
    # `code_location_register` and `namespace_registry` -- ALL of them
    # `*_register`/`*_registry`, i.e. in scope. NONE of these 23 is written by a
    # gate; each sets its own flag in its create/upsert path. An `is_active`
    # nobody gates is not a proven-activation flag, so a 100-run cannot decide it.
    # ---------------------------------------------------------------------
    # MAPPING / BINDING: pairs two things. (`role_environment` is literally a
    # `pair_id`; `is_primary` marks the primary pairing, not a proof.)
    "assignee_selection": "MAPPING: a (slot, identity, role) selection, not a proven thing",
    "coordinate_session": "MAPPING: pairs a session with a coordinate capture",
    "llm_model_alias": "MAPPING: an alias -> model pairing",
    "pick_flow": "MAPPING: a (slot, environment, llm, tool, role) pick",
    "question_template_factor": "MAPPING: a (combo, factor, scoring) pairing",
    "role_environment": ("MAPPING: a `pair_id` linking a role to an environment. "
                         "`is_primary` marks the primary pair, not a proof"),
    "worker_environment_binding": ("MAPPING: pairs a worker with an environment "
                                   "(`is_primary` marks the primary pair)"),
    "worker_identity_binding": ("MAPPING: pairs a worker with an identity "
                                "dimension"),
    "worker_job_binding": ("MAPPING: pairs a worker with a job/task type; it "
                           "carries `gate_ref`, so it POINTS AT a gate rather "
                           "than being one"),
    "workflow_playwright": "MAPPING: pairs a workflow with a playwright runner",
    "target_group": "MAPPING: groups targets within an environment",
    # VOCABULARY / DEFINITION: a value or template the system is expressed in,
    # the same shape as the already-exempt `prompt_dimension` / `channel`.
    "environment_configure": ("VOCABULARY: a captured coordinate set for an "
                              "environment"),
    "environment_configure_template": "VOCABULARY: a reusable coordinate template",
    "environment_template": "VOCABULARY: an environment's template definition",
    "playwright_environment": "VOCABULARY: an automation environment definition",
    "playwright_step": ("VOCABULARY: one step of an automation script. Its own "
                        "`proof` column carries the step's evidence, so a "
                        "100-run cannot decide a step"),
    "target_template": "VOCABULARY: a target template, not a proven thing",
    "tool_center": ("VOCABULARY: the tool catalog entry, the same shape as the "
                    "already-exempt `ticket_center`"),
    "vscode_env_dimension": ("VOCABULARY: a VS Code environment dimension, the "
                             "same shape as the exempt `prompt_dimension`"),
    "working_environment": ("BASE: the working-environment definition, the same "
                            "shape as the exempt `channel` / `module`"),
    # PHASE: a step in a process, not a proven artifact.
    # MERGED 2026-09-27 (plan REGISTER.NAMING.AND.PHASE.MERGE): the two phase
    # tables became ONE (`phase_register`), so the exemption is now ONE name.
    # The two old names are kept because a stale process may still ask.
    "phase_register": "PHASE: a step of a process (register-fill or sweep)",
    "register_fill_phase": "PHASE: a step of the register-fill process",
    "terminology_sweep_phase": "PHASE: a step of the terminology sweep",
    # WORK ITEM: an activity that is opened and closed, not proven.
    "chat": ("WORK ITEM: a conversation is opened and closed, the same shape as "
             "the exempt `ticket`"),
}


def tables_with_is_active(conn: sqlite3.Connection) -> list[str]:
    """Every table that declares an `is_active` column, measured from the DB."""
    out: list[str] = []
    for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        t = str(r[0])
        if t.startswith("sqlite_"):
            continue
        cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)}
        if "is_active" in cols:
            out.append(t)
    return out


def activation_scope(conn: sqlite3.Connection) -> dict[str, Any]:
    """The tables the backfill MAY touch, and the ones it MUST NOT.

    Returns `{in_scope, exempt, unclassified}`. `unclassified` is a table with
    `is_active` that is neither in scope nor exempt — it is REPORTED, never
    silently included or silently skipped.
    """
    all_t = tables_with_is_active(conn)
    in_scope = [t for t in all_t
                if t.endswith(ACTIVATION_SCOPE_SUFFIXES)
                and t not in NO_ACTIVATION_BECAUSE]
    exempt = [t for t in all_t if t in NO_ACTIVATION_BECAUSE]
    unclassified = [t for t in all_t
                    if t not in in_scope and t not in exempt]
    return {"in_scope": in_scope, "exempt": exempt,
            "unclassified": unclassified, "all": all_t}


# ---------------------------------------------------------------------------
# The streak, read from the 100-run evidence
# ---------------------------------------------------------------------------

def streak_for(
    conn: sqlite3.Connection,
    ref_tag: str,
    *,
    rule_version: int = 1,
    model: str | None = None,
) -> int:
    """Consecutive `win=1` rounds for a ref_tag, newest first.

    Delegates to `llm_100_run_harness.current_streak` so there is ONE definition
    of "streak". A second implementation would let the gate and the harness
    disagree about the same evidence.
    """
    import llm_100_run_harness as h
    if model is None:
        model = str(h.resolve_model(conn)["model"])
    return int(h.current_streak(conn, ref_tag, int(rule_version), model))

def streak_detail(
    conn: sqlite3.Connection,
    ref_tag: str,
    *,
    rule_version: int = 1,
    model: str | None = None,
) -> dict[str, Any]:
    """The streak WITH its denominator and its provider source.

    The denominator is the point: `_drive_streaks.py` measured that the probes
    are deterministic, so "100 runs" of ONE case is "1 test x100". Reporting the
    streak without the number of DISTINCT rounds is how a figure gets read as
    more than it is.
    """
    import llm_100_run_harness as h
    resolved = h.resolve_model(conn)
    mdl = model or str(resolved["model"])
    rows = list(conn.execute(
        "SELECT win FROM proof_run WHERE ref_tag=? AND rule_version=? "
        "AND model=? ORDER BY id DESC", (ref_tag, int(rule_version), mdl)))
    streak = 0
    for r in rows:
        if int(r[0]) == 1:
            streak += 1
        else:
            break
    return {
        "ref_tag": ref_tag,
        "rule_version": int(rule_version),
        "model": mdl,
        "provider_source": resolved.get("source"),
        "streak": streak,
        "rounds": len(rows),
        "wins": sum(1 for r in rows if int(r[0]) == 1),
    }


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def is_activated(
    conn: sqlite3.Connection,
    letter: str,
    ref_id: int,
    version: int,
) -> bool:
    """Is THIS VERSION active? (Q6: per version, not per row.)"""
    row = conn.execute(
        "SELECT is_active FROM version_register WHERE entity_type=? "
        "AND entity_ref_id=? AND version=?",
        (str(letter).strip().upper(), int(ref_id), int(version))).fetchone()
    return bool(row and int(row[0]) == 1)


def two_part_verdict(
    conn: sqlite3.Connection,
    ref_tag: str,
) -> dict[str, Any]:
    """The 2-part verdict for the entity behind a `ref_tag`, MEASURED.

    MEASURED DEFECT THIS CLOSES (2026-09-26). `assert_may_activate` checked the
    STREAK and nothing else:

        d = streak_detail(...)
        if d["rounds"] == 0:  return False, "no 100-run evidence ..."
        if d["streak"] < target:  return False, "streak ... < target ..."
        return True, "streak ... >= target ..."

    MEASURED: `activation_gate.py` mentioned `register_approve` in exactly TWO
    places, and NEITHER was a check — `:104` is a comment and `:134` is the
    `NO_ACTIVATION_BECAUSE` exemption. `two_part`, `WORKABLE`, `APPROVED`,
    `tdd_pass` and `ontology_pass` appeared **0 times**.

    THE CONSEQUENCE: a REFUSED 2-part verdict would flip `is_active` just as
    readily as an APPROVED one. That is the SAME defect this session fixed in
    `llm_100_run_harness` (a streak alone flipping `is_active`), still present at
    the gate layer — and the gate is the ONE writer, so it is the layer that
    matters. The 2-part verify exists to make a streak INSUFFICIENT; if the gate
    ignores it, the verify is advisory, and an advisory gate is a gate that does
    not exist.

    Returns `{ok, verdict, class, entity, why, cite}`. `ok=False` with a `why`
    is a REFUSAL. A MISSING row is a REFUSAL, not a pass — an entity nobody
    approved is not an approved entity.
    """
    import prompt_register as pr

    resolved = pr.resolve_ref_tag(conn, ref_tag)
    kind = str(resolved.get("kind") or "")
    letter = None
    ref_id = None
    if kind == "prompt":
        p = pr.get_prompt(conn, ref_tag)
        if p:
            # MEASURED (2026-09-26): `P`'s pk_column is `prompt_id`, NOT
            # `skill_id`. Using `skill_id` addresses a DIFFERENT prompt.
            letter, ref_id = "P", int(p["prompt_id"])
    elif kind == "entity_id":
        letter = str(resolved.get("letter") or "")
        ref_id = int(resolved.get("ref_id") or 0)

    if not letter or not ref_id:
        return {"ok": False, "verdict": None, "class": None,
                "entity": {"type": letter, "ref_id": ref_id},
                "why": ("ref_tag %r resolves to kind %r, which has no entity to "
                        "read a 2-part verdict from" % (ref_tag, kind)),
                "cite": "activation_gate.py:two_part_verdict"}

    try:
        row = conn.execute(
            "SELECT verdict, class, tdd_pass, tdd_verify_pass, ontology_pass "
            "FROM register_approve WHERE entity_type=? AND entity_ref_id=? "
            "ORDER BY id DESC LIMIT 1",
            (letter, int(ref_id))).fetchone()
    except sqlite3.OperationalError as e:
        return {"ok": False, "verdict": None, "class": None,
                "entity": {"type": letter, "ref_id": ref_id},
                "why": ("register_approve is absent (%s), so no 2-part verdict "
                        "can be read — an unreadable verdict is a REFUSAL, not a "
                        "pass" % e),
                "cite": "activation_gate.py:two_part_verdict"}

    if not row:
        return {"ok": False, "verdict": None, "class": None,
                "entity": {"type": letter, "ref_id": ref_id},
                "why": ("no register_approve row for %s/%d — an entity nobody "
                        "approved is NOT an approved entity"
                        % (letter, int(ref_id))),
                "cite": "activation_gate.py:two_part_verdict"}

    d = dict(row)
    verdict = str(d.get("verdict") or "")
    if verdict != "APPROVED":
        return {"ok": False, "verdict": verdict, "class": d.get("class"),
                "entity": {"type": letter, "ref_id": ref_id},
                "why": ("the 2-part verdict for %s/%d is %r, not APPROVED "
                        "(tdd=%s tdd_verify=%s ontology=%s)"
                        % (letter, int(ref_id), verdict, d.get("tdd_pass"),
                           d.get("tdd_verify_pass"), d.get("ontology_pass"))),
                "cite": "activation_gate.py:two_part_verdict"}

    return {"ok": True, "verdict": verdict, "class": d.get("class"),
            "entity": {"type": letter, "ref_id": ref_id},
            "why": ("the 2-part verdict for %s/%d is APPROVED (class=%s)"
                    % (letter, int(ref_id), d.get("class"))),
            "cite": "activation_gate.py:two_part_verdict"}


def assert_may_activate(
    conn: sqlite3.Connection,
    ref_tag: str,
    *,
    target: int = DEFAULT_TARGET,
    rule_version: int = 1,
    model: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """May this ref_tag be activated? Returns `(ok, reason, detail)`.

    Returns a REASON rather than a bare bool: a caller that only gets `False`
    cannot say WHY, and "not proven" and "no evidence at all" need different
    responses.

    TWO CHECKS, and BOTH must pass (2026-09-26):

      1. THE STREAK — the original check, UNCHANGED.
      2. THE 2-PART VERDICT — MEASURED DEFECT this closes: the streak was the
         ONLY check, so a REFUSED 2-part verdict would flip `is_active` just as
         readily as an APPROVED one. The 2-part verify exists to make a streak
         INSUFFICIENT; a gate that ignores it is advisory, and an advisory gate
         is a gate that does not exist.

    The streak is checked FIRST so a below-target refusal still names the streak
    (the more common case), and the verdict is checked SECOND.
    """
    d = streak_detail(conn, ref_tag, rule_version=rule_version, model=model)
    if d["rounds"] == 0:
        return False, ("no 100-run evidence for ref_tag %r (0 rounds) — an "
                       "unrun thing is UNPROVEN, not proven" % ref_tag), d
    if d["streak"] < int(target):
        return False, ("streak %d < target %d (of %d rounds, %d wins)"
                       % (d["streak"], int(target), d["rounds"], d["wins"])), d
    # ---- CHECK 2: THE 2-PART VERDICT --------------------------------------
    v = two_part_verdict(conn, ref_tag)
    d["two_part"] = v
    if not v.get("ok"):
        return False, ("streak %d >= target %d, BUT the 2-part verify does not "
                       "approve: %s" % (d["streak"], int(target), v.get("why"))), d
    return True, ("streak %d >= target %d AND the 2-part verdict is APPROVED "
                  "(class=%s)" % (d["streak"], int(target), v.get("class"))), d


# ---------------------------------------------------------------------------
# RETIRE — the OTHER half of "this version is now the active one"
# ---------------------------------------------------------------------------
# THE BUG THIS CLOSES (MEASURED 2026-09-27). THE HUMAN:
#
#     "problem is proof run > new version + and missing to is_active -> 0 for
#     old version / that is BUG! fix it now"
#
# `activate()` wrote `is_active=1` on the NEW version and left the OLD version
# at `1`. `ensure_version()` inserted a new version and touched nothing. So an
# entity could have TWO active versions at once — MEASURED: `G/39` versions
# 1 and 2 both `is_active=1`, with `version_cleanup` recording the
# supersession and NO `is_active=0` to match it.
#
# WHY IT LIVES HERE AND NOT IN A NEW MODULE: `activation_gate` already declares
# itself "THE ONE writer of `is_active = 1`" (module docstring). "Which version
# is active" is ONE question, so it gets ONE answer, next to the activate it
# belongs with. A second implementation would be two answers to one question.
#
# WHY IT ALSO RECORDS `version_cleanup`: an activation that retires a version
# WITHOUT recording why is indistinguishable from a soft-delete, and the repo's
# own law (`db_schema.py:1653`) reserves `is_active=0` for exactly that meaning.
# Record AND flip, never one without the other.


def retire_other_versions(
    conn: sqlite3.Connection,
    letter: str,
    ref_id: int,
    *,
    keep_version: int,
    reason: str,
    cite_ref: str,
    decided_by: str = "activation_gate",
    commit: bool = False,
) -> dict[str, Any]:
    """Retire every version of `{letter}/{ref_id}` EXCEPT `keep_version`.

    THE INVARIANT: at most ONE version of an entity is `is_active = 1`.

    For every other version row that is CURRENTLY active, this sets
    `is_active = 0` AND writes the supersession to `version_cleanup` — in one
    transaction, so a half-done retire cannot exist. A row already at `0` is
    left alone and REPORTED as `already_inactive` rather than flipped again,
    because a re-flip would invent a supersession that did not happen.

    REFUSES without a checkable citation, the same way `activate()` and
    `record_supersession()` do. An uncited retire is a claim.

    Returns `{ok, kept, retired: [versions], already_inactive: [...], cleanup_ids}`.
    """
    letter = str(letter).strip().upper()
    if int(keep_version) < 1:
        return {"ok": False, "code": "BAD_KEEP_VERSION",
                "message": "keep_version must be >= 1"}
    if not str(reason or "").strip():
        return {"ok": False, "code": "MISSING_REASON",
                "message": "a retirement needs a reason"}
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    rows = list(conn.execute(
        "SELECT version_register_id, version, is_active FROM version_register "
        "WHERE entity_type = ? AND entity_ref_id = ? AND version <> ? "
        "ORDER BY version",
        (letter, int(ref_id), int(keep_version))))

    retired: list[int] = []
    already: list[int] = []
    cleanup_ids: list[int] = []
    for r in rows:
        if int(r["is_active"]) != 1:
            already.append(int(r["version"]))
            continue
        conn.execute(
            "UPDATE version_register SET is_active = 0 "
            "WHERE version_register_id = ?", (int(r["version_register_id"]),))
        cur = conn.execute(
            "INSERT INTO version_cleanup (entity_type, entity_ref_id, "
            "old_version, new_version, reason, cite_ref, decided_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (letter, int(ref_id), int(r["version"]), int(keep_version),
             str(reason), str(cite_ref), str(decided_by)))
        cleanup_ids.append(int(cur.lastrowid))
        retired.append(int(r["version"]))

    if commit:
        conn.commit()
    return {"ok": True, "letter": letter, "ref_id": int(ref_id),
            "kept": int(keep_version), "retired": retired,
            "already_inactive": already, "cleanup_ids": cleanup_ids}


def activate(
    conn: sqlite3.Connection,
    letter: str,
    ref_id: int,
    version: int,
    *,
    ref_tag: str,
    cite_ref: str,
    target: int = DEFAULT_TARGET,
    rule_version: int = 1,
    model: str | None = None,
    decided_by: str = "activation_gate",
    commit: bool = True,
) -> dict[str, Any]:
    """THE ONLY WRITER of `is_active = 1`. Refuses unless the streak proves it.

    Every refusal is a RETURNED dict, not an exception, so a caller can report
    WHICH gate refused. The one exception is a programming error (a missing
    citation module), which is not a gate outcome.
    """
    letter = str(letter).strip().upper()
    ref_tag = str(ref_tag or "").strip()
    if not ref_tag:
        return {"ok": False, "code": "MISSING_REF_TAG",
                "message": "ref_tag is required (it is the 100-run key)"}

    # THE REF_TAG MUST RESOLVE (2026-09-22). MEASURED: `ref_tag` is FREE TEXT
    # (`coord_store.py:73`) and this function used to take a bare string, so a
    # prompt could CLAIM a streak that belongs to something else. A streak that
    # belongs to nothing cannot prove anything.
    #
    # MEASURED, and it shaped the rule: the ONE existing `ref_tag` is `1.1F`,
    # which resolves to a `field_tdd_rule.slice_key`, NOT a prompt. So the rule
    # is "must resolve to a KNOWN proof target", not "must be a prompt".
    try:
        import prompt_register as pr
        resolved = pr.resolve_ref_tag(conn, ref_tag)
        if not resolved.get("ok"):
            return {"ok": False, "code": "UNRESOLVED_REF_TAG",
                    "message": resolved.get("message")}
    except ImportError:
        resolved = {"kind": "unknown"}

    # The citation gate. An activation with no checkable reference is a claim.
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    ok, reason, detail = assert_may_activate(
        conn, ref_tag, target=target, rule_version=rule_version, model=model)
    if not ok:
        return {"ok": False, "code": "NOT_PROVEN", "message": reason,
                "streak": detail}

    row = conn.execute(
        "SELECT version_register_id, is_active FROM version_register "
        "WHERE entity_type=? AND entity_ref_id=? AND version=?",
        (letter, int(ref_id), int(version))).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_VERSION",
                "message": ("no version_register row for %s-%d-%d — a version "
                            "must exist before it can be proven"
                            % (letter, int(ref_id), int(version)))}

    if int(row["is_active"]) == 1:
        # ALREADY ACTIVE — but the INVARIANT STILL HAS TO HOLD.
        # MEASURED DEFECT IN MY OWN FIRST VERSION (2026-09-27): this returned
        # early, so a version that was ALREADY `1` never retired the others.
        # That is the bug reappearing through the other door: if v1 is active
        # and v2 was flipped by the OLD buggy code, `activate(v1)` returned
        # `ALREADY_ACTIVE` and v2 STAYED ACTIVE. Measured by the proof
        # (`_proof_version_flip.py` QC-06), which is what it is for.
        # So the retire runs on BOTH paths; only the FLIP is skipped.
        retired = retire_other_versions(
            conn, letter, int(ref_id), keep_version=int(version),
            reason=("version %d supersedes it: %s/%d version %d is the active "
                    "one (activation was already recorded)"
                    % (int(version), letter, int(ref_id), int(version))),
            cite_ref=cite_ref,
            decided_by=decided_by, commit=False)
        if commit:
            conn.commit()
        return {"ok": True, "code": "ALREADY_ACTIVE", "changed": False,
                "version_register_id": int(row["version_register_id"]),
                "letter": letter, "ref_id": int(ref_id),
                "version": int(version),
                "retired": retired.get("retired") or [],
                "streak": detail}

    conn.execute(
        "UPDATE version_register SET is_active=1 WHERE version_register_id=?",
        (int(row["version_register_id"]),))
    # ---- THE OTHER HALF: retire what this version REPLACES -----------------
    # MEASURED BUG (2026-09-27): without this, activating version 2 left
    # version 1 at `is_active=1`, so the entity had TWO active versions.
    # Same transaction (`commit=False`): the activate and the retire land
    # together or not at all, so a half-done flip cannot exist.
    retired = retire_other_versions(
        conn, letter, int(ref_id), keep_version=int(version),
        reason=("version %d supersedes it: activating version %d for %s/%d"
                % (int(version), int(version), letter, int(ref_id))),
        cite_ref=cite_ref,
        decided_by=decided_by, commit=False)
    if commit:
        conn.commit()
    return {"ok": True, "code": "ACTIVATED", "changed": True,
            "version_register_id": int(row["version_register_id"]),
            "letter": letter, "ref_id": int(ref_id), "version": int(version),
            "cite_ref": cite_ref, "decided_by": decided_by,
            "retired": retired.get("retired") or [],
            "streak": detail}


def activate_flow(
    conn: sqlite3.Connection,
    workflow_key: str,
    *,
    ref_tag: str,
    cite_ref: str,
    target: int = DEFAULT_TARGET,
    rule_version: int = 1,
    model: str | None = None,
    decided_by: str = "activation_gate",
    commit: bool = True,
) -> dict[str, Any]:
    """THE ONLY WRITER of `workflow_register.is_active = 1`.

    The user (2026-09-22):
        "flow is you have the system and register at the table > is_active = 0
         and need to proofed is that work with proof run report
         so who make the system and who QC for work"

    WHY A SEPARATE FUNCTION AND NOT `activate()`
    --------------------------------------------
    MEASURED: `activate()` takes an ENTITY ID (`letter`, `ref_id`, `version`) and
    writes `version_register`. A FLOW has no entity letter — it is a
    `workflow_register` row keyed by `workflow_key`. So `activate()` cannot
    address a flow at all, and a flow had NO way to be activated by the gate.

    The GATE ITSELF IS REUSED, not re-implemented: `assert_may_activate` decides
    whether the streak proves it, and the citation gate is the same one. A second
    streak rule would let the gate and the harness disagree about the same
    evidence.

    `workflow_register` is IN the activation scope (`ACTIVATION_SCOPE_SUFFIXES`
    matches `_register`), so it is NOT in `NO_ACTIVATION_BECAUSE` — a flow SHOULD
    require a proof.
    """
    key = str(workflow_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_WORKFLOW_KEY",
                "message": "workflow_key is required"}
    ref_tag = str(ref_tag or "").strip()
    if not ref_tag:
        return {"ok": False, "code": "MISSING_REF_TAG",
                "message": "ref_tag is required (it is the proof-run key)"}

    # THE FLOW MUST EXIST FIRST. DEFECT FOUND BY RUNNING THE PROOF (2026-09-22):
    # the first version checked the STREAK before the flow's existence, so a
    # missing flow was reported as `NOT_PROVEN` — "not proven" and "does not
    # exist" need different responses, and a caller told "not proven" would go
    # looking for evidence for a flow that is not there.
    row = conn.execute(
        "SELECT workflow_id, is_active FROM workflow_register WHERE "
        "workflow_key=?", (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_FLOW",
                "message": ("no workflow_register row for %r — a flow must exist "
                            "before it can be proven" % key)}

    # The citation gate. An activation with no checkable reference is a claim.
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    ok, reason, detail = assert_may_activate(
        conn, ref_tag, target=target, rule_version=rule_version, model=model)
    if not ok:
        return {"ok": False, "code": "NOT_PROVEN", "message": reason,
                "streak": detail,
                # THE 2-PART VERDICT, REPORTED. MEASURED (2026-09-26): the gate
                # used to check the streak ONLY, so a REFUSED 2-part verdict
                # would flip `is_active` just as readily as an APPROVED one.
                "two_part": detail.get("two_part")}

    if int(row["is_active"]) == 1:
        return {"ok": True, "code": "ALREADY_ACTIVE", "changed": False,
                "workflow_id": int(row["workflow_id"]), "streak": detail,
                "two_part": detail.get("two_part")}

    conn.execute(
        "UPDATE workflow_register SET is_active=1, "
        "updated_at=datetime('now') WHERE workflow_id=?",
        (int(row["workflow_id"]),))
    if commit:
        conn.commit()
    return {"ok": True, "code": "ACTIVATED", "changed": True,
            "workflow_id": int(row["workflow_id"]), "workflow_key": key,
            "cite_ref": cite_ref, "decided_by": decided_by, "streak": detail,
            # THE EVIDENCE THE FLIP RESTS ON, REPORTED so a reader can see BOTH
            # checks passed rather than taking the flip on trust.
            "two_part": detail.get("two_part")}


# ---------------------------------------------------------------------------
# Supersession — the cleanup table (Q6)
# ---------------------------------------------------------------------------

def record_supersession(
    conn: sqlite3.Connection,
    letter: str,
    ref_id: int,
    *,
    old_version: int,
    new_version: int,
    reason: str,
    cite_ref: str,
    decided_by: str = "activation_gate",
    commit: bool = True,
) -> dict[str, Any]:
    """Record that `new_version` supersedes `old_version`.

    It does NOT delete and it does NOT flip `is_active`. It RECORDS the
    supersession, so "old version" becomes a FACT with a citation rather than an
    inference from a `0` — which is the only way to keep the two meanings of
    `is_active` from being confused.
    """
    letter = str(letter).strip().upper()
    if int(old_version) == int(new_version):
        return {"ok": False, "code": "SAME_VERSION",
                "message": "old_version and new_version are both %d"
                           % int(old_version)}
    if not str(reason or "").strip():
        return {"ok": False, "code": "MISSING_REASON",
                "message": "a supersession needs a reason"}
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    for v in (int(old_version), int(new_version)):
        if not conn.execute(
                "SELECT 1 FROM version_register WHERE entity_type=? "
                "AND entity_ref_id=? AND version=?",
                (letter, int(ref_id), v)).fetchone():
            return {"ok": False, "code": "NO_SUCH_VERSION",
                    "message": "no version_register row for %s-%d-%d"
                               % (letter, int(ref_id), v)}

    cur = conn.execute(
        "INSERT INTO version_cleanup (entity_type, entity_ref_id, old_version, "
        "new_version, reason, cite_ref, decided_by) VALUES (?,?,?,?,?,?,?)",
        (letter, int(ref_id), int(old_version), int(new_version),
         str(reason), str(cite_ref), str(decided_by)))
    if commit:
        conn.commit()
    return {"ok": True, "cleanup_id": cur.lastrowid, "letter": letter,
            "ref_id": int(ref_id), "old_version": int(old_version),
            "new_version": int(new_version)}


def supersessions_of(conn: sqlite3.Connection, letter: str,
                     ref_id: int) -> list[dict[str, Any]]:
    """The supersession history of one entity, oldest first.

    Ordered by `cleanup_id`, the table's OWN primary key. My first version said
    `ORDER BY id`, which is the column name every OTHER table in this repo uses
    — so it read as correct and raised `no such column: id` only when run.
    """
    rows = conn.execute(
        "SELECT * FROM version_cleanup WHERE entity_type=? AND entity_ref_id=? "
        "ORDER BY cleanup_id", (str(letter).strip().upper(), int(ref_id)))
    return [dict(r) for r in rows]


def record_flow_supersession(
    conn: sqlite3.Connection,
    old_flow_key: str,
    new_flow_key: str,
    *,
    reason: str,
    cite_ref: str,
    decided_by: str = "activation_gate",
    commit: bool = True,
) -> dict[str, Any]:
    """Record that `new_flow_key` supersedes `old_flow_key`.

    WHY A SEPARATE FUNCTION (2026-09-22): `record_supersession` requires BOTH
    versions to exist in `version_register`, and a FLOW has no entity letter — it
    is a `workflow_register` row. MEASURED: calling it for a flow returned
    `NO_SUCH_VERSION`, so a flow's retirement had NOWHERE to be recorded.

    The record goes in `version_cleanup` with `entity_type='K'` and
    `entity_ref_id` = the OLD flow's `workflow_id`.

    DEFECT FOUND BY RUNNING `_proof_contract_ref.py` (2026-09-22): the first
    version used `entity_type='FLOW'`, which VIOLATED the FK to
    `entity_type_register` — the proof reported a NEW violation. MEASURED: the
    register already has `K` = `workflow` -> `workflow_register`, so the letter
    EXISTS and must be used rather than invented.

    It does NOT delete and it does NOT flip `is_active`. It RECORDS.
    """
    old_key = str(old_flow_key or "").strip()
    new_key = str(new_flow_key or "").strip()
    if not old_key or not new_key:
        return {"ok": False, "code": "MISSING_FLOW_KEY",
                "message": "both flow keys are required"}
    if old_key == new_key:
        return {"ok": False, "code": "SAME_FLOW",
                "message": "old and new flow are both %r" % old_key}
    if not str(reason or "").strip():
        return {"ok": False, "code": "MISSING_REASON",
                "message": "a supersession needs a reason"}
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    old = conn.execute("SELECT workflow_id FROM workflow_register WHERE "
                       "workflow_key=?", (old_key,)).fetchone()
    if not old:
        return {"ok": False, "code": "NO_SUCH_FLOW",
                "message": "no workflow_register row for %r" % old_key}
    new = conn.execute("SELECT workflow_id FROM workflow_register WHERE "
                       "workflow_key=?", (new_key,)).fetchone()
    if not new:
        return {"ok": False, "code": "NO_SUCH_FLOW",
                "message": "no workflow_register row for %r" % new_key}

    cur = conn.execute(
        "INSERT INTO version_cleanup (entity_type, entity_ref_id, old_version, "
        "new_version, reason, cite_ref, decided_by) VALUES (?,?,?,?,?,?,?)",
        ("K", int(old["workflow_id"]), 1, 2,
         "%s supersedes %s: %s" % (new_key, old_key, str(reason)),
         str(cite_ref), str(decided_by)))
    if commit:
        conn.commit()
    return {"ok": True, "cleanup_id": cur.lastrowid, "old_flow": old_key,
            "new_flow": new_key, "old_workflow_id": int(old["workflow_id"])}


def flow_supersessions_of(conn: sqlite3.Connection, flow_key: str
                          ) -> list[dict[str, Any]]:
    """The supersession history of one flow, oldest first.

    `entity_type='K'` is the register's own letter for `workflow`
    (`entity_type_register`: K -> workflow_register).
    """
    row = conn.execute("SELECT workflow_id FROM workflow_register WHERE "
                       "workflow_key=?", (str(flow_key),)).fetchone()
    if not row:
        return []
    rows = conn.execute(
        "SELECT * FROM version_cleanup WHERE entity_type='K' AND "
        "entity_ref_id=? ORDER BY cleanup_id", (int(row["workflow_id"]),))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def activation_key(conn: sqlite3.Connection,
                   model: str | None = None) -> dict[str, Any]:
    """THE KEY for `is_active = 0 -> 1`, per ref_tag, in ONE report.

    THE HUMAN (2026-09-25): "proof run, is the key for is_active=0 -> 1".

    THE ANSWER, MEASURED
    --------------------
    The key is a **PROVEN STREAK**, not a flag, not a count, not a date. The
    mechanism already exists (`activate()`), and it is **UNWIRED**: MEASURED,
    `llm_100_run_harness.py` does not mention `activation_gate` at all, so the
    harness runs the 100-run, records `proof_run`, and NEVER calls `activate()`.

    MEASURED: `worker_identity` has streak **40 >= target 20**, so
    `assert_may_activate` returns **True** — and nothing flipped. A gate that is
    never called is a gate that does not exist.

    This report makes the key VISIBLE per ref_tag: the streak, the target, the
    verdict, and WHAT WOULD FLIP. It reads; it never writes.
    """
    import llm_100_run_harness as h

    tags = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT ref_tag FROM proof_run "
        " WHERE ref_tag IS NOT NULL AND ref_tag <> '' ORDER BY ref_tag")]
    rows: list[dict[str, Any]] = []
    for tag in tags:
        try:
            rv = int(h.current_rule_version(conn, tag))
        except Exception:
            rv = 1
        try:
            tgt = h.streak_target_for(conn, tag)
            target = int(tgt.get("target") or DEFAULT_TARGET)
        except Exception as e:
            tgt = {"target": DEFAULT_TARGET, "source": "error: %s" % e}
            target = DEFAULT_TARGET
        try:
            d = streak_detail(conn, tag, rule_version=rv, model=model)
        except Exception as e:
            d = {"streak": 0, "rounds": 0, "wins": 0, "error": str(e)}
        ok, reason, _ = assert_may_activate(
            conn, tag, target=target, rule_version=rv, model=model)
        rows.append({
            "ref_tag": tag, "rule_version": rv,
            "streak": int(d.get("streak") or 0),
            "rounds": int(d.get("rounds") or 0),
            "wins": int(d.get("wins") or 0),
            "target": target, "target_source": tgt.get("source"),
            "may_activate": bool(ok), "reason": reason,
            "what_would_flip": (
                "activate() would flip is_active 0 -> 1 for this ref_tag's "
                "version" if ok else
                "needs %d more consecutive wins (streak %d of %d)"
                % (max(0, target - int(d.get("streak") or 0)),
                   int(d.get("streak") or 0), target)),
        })
    return {"ok": True, "model": model, "ref_tags": rows,
            "the_key": ("a PROVEN STREAK: current_streak(ref_tag, rule_version, "
                        "model) >= streak_target_for(ref_tag)"),
            "note": ("the gate is the ONE writer of is_active=1; this report "
                     "reads and never writes")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the activation gate")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--scope", action="store_true",
                    help="print the activation scope and the exemptions")
    ap.add_argument("--status", metavar="REF_TAG",
                    help="print the streak detail for a ref_tag")
    ap.add_argument("--key", action="store_true",
                    help="THE KEY for is_active=0 -> 1, per ref_tag")
    ap.add_argument("--model", default=None,
                    help="the model whose streak is measured (default: the "
                         "harness's own resolution)")
    ap.add_argument("--target", type=int, default=DEFAULT_TARGET)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        if args.scope:
            sc = activation_scope(conn)
            print("=== activation scope ===")
            print("  tables with is_active : %d" % len(sc["all"]))
            print("  IN SCOPE (%d):" % len(sc["in_scope"]))
            for t in sc["in_scope"]:
                print("    %s" % t)
            print("  EXEMPT (%d):" % len(sc["exempt"]))
            for t in sc["exempt"]:
                print("    %-24s %s" % (t, NO_ACTIVATION_BECAUSE[t]))
            print("  UNCLASSIFIED (%d):" % len(sc["unclassified"]))
            for t in sc["unclassified"]:
                print("    %s" % t)
            return 0

        if args.key:
            k = activation_key(conn, model=args.model)
            print("=== THE KEY for is_active=0 -> 1 ===")
            print("  %s" % k["the_key"])
            print()
            print("  %-24s %-4s %7s %7s %7s %-6s %s"
                  % ("ref_tag", "rv", "streak", "target", "rounds", "flip",
                     "what would flip"))
            print("  " + "-" * 112)
            for r in k["ref_tags"]:
                print("  %-24s %-4d %7d %7d %7d %-6s %s"
                      % (r["ref_tag"], r["rule_version"], r["streak"],
                         r["target"], r["rounds"],
                         "YES" if r["may_activate"] else "no",
                         r["what_would_flip"][:56]))
            n_ready = sum(1 for r in k["ref_tags"] if r["may_activate"])
            print()
            print("  %d of %d ref_tags MEET their target and would flip."
                  % (n_ready, len(k["ref_tags"])))
            return 0

        if args.status:
            d = streak_detail(conn, args.status)
            ok, reason, _ = assert_may_activate(
                conn, args.status, target=args.target)
            print(json.dumps(d, indent=2, ensure_ascii=False))
            print("  may_activate : %s" % ok)
            print("  reason       : %s" % reason)
            return 0

        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
