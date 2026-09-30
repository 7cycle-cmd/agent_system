# -*- coding: utf-8 -*-
"""prompt_registry.py — a PROMPT is a HYPOTHESIS WITH A MEASURED UNIT.

WHY THIS EXISTS (user, 2026-09-22)
----------------------------------
    "first_principle can be the qc pass / fail for prompt output?
     output need to have this test to = is_active at prompt_registry
     so prompt is design for proof for target / is it correct?"

YES, and this module makes it checkable. A prompt is NOT text. It is a
HYPOTHESIS: "this wording makes the model answer correctly". The five
first-principle answers DEFINE what "correctly" means:

    failure_mode  how this prompt could answer wrongly
    observable    what a third party can see
    unit          the MEASURED UNIT   <- "measured unit is the key"
    threshold     how many wins = pass
    independence  can this prompt fail alone?

Without the unit, "pass" is an opinion. With it, "pass" is a number.

THE CHAIN
---------
    prompt (a hypothesis)
      -> first_principle DEFINES "correct"
      -> oracle() IMPLEMENTS the definition (ground truth)
      -> 100-run (llm_100_run: value, oracle_answer, llm_answer, win)
      -> is_active=1 (activation_gate: streak >= target)

WHY `ref_tag` MUST RESOLVE
--------------------------
MEASURED 2026-09-22: `ref_tag` is FREE TEXT (`coord_store.py:73`) and
`activation_gate.assert_may_activate(conn, ref_tag, ...)` takes a bare string, so
a prompt could CLAIM a streak that belongs to something else.

MEASURED, and it changed the design: the ONE existing `ref_tag` is `1.1F`, which
does NOT resolve to a `prompt_key` — it is a `field_tdd_rule.slice_key` (the
phone field). So `ref_tag` is NOT always a prompt. It is a PROOF TARGET, and a
proof target may be a prompt OR a field rule. `resolve_ref_tag` therefore reports
WHICH KIND it resolved to, rather than assuming.

WHY THE FORMULA CAN BE IMPROVED
-------------------------------
    "as prompt is generatot by formula, so we can have enough detail to help for
     improve formula design"

A prompt is generated from `wording_registry` dimensions x values, joined by
`prompt_wording` (`UNIQUE (prompt_id, dim_key)`). So a failure must be
attributable to a DIMENSION, or the formula can only be improved from opinion.
`llm_100_run` has `failure_reason` but no `dim_key` — that is the missing link,
and `attribute_failure` is where it is closed.

Run:
    .\\.venv\\Scripts\\python.exe prompt_registry.py --list
    .\\.venv\\Scripts\\python.exe prompt_registry.py --resolve 1.1F
    .\\.venv\\Scripts\\python.exe prompt_registry.py --unproven
"""
from __future__ import annotations

import argparse
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

# The five answers, IMPORTED from the ONE place they are declared. A second copy
# here is the drift `factor_first_principle`'s own mapping exists to remove.
import factor_first_principle as fp  # noqa: E402

ANSWER_COLUMNS: tuple[str, ...] = tuple(
    k for k, _q, _d in fp.FIRST_PRINCIPLE_QUESTIONS)

# The kinds a `ref_tag` can resolve to.
#
# MEASURED 2026-09-22, and it changed the design THREE times:
#   * the ONE existing `ref_tag` is `1.1F`, which does NOT resolve to a
#     `prompt_key` — so a single-kind assumption would have been wrong;
#   * it is not a `field_tdd_rule.slice_key` either (those are field NAMES:
#     `phone`, `name`, `region`);
#   * the user then said: "1.1F is old task is now is entity id". So `1.1F` is a
#     LEGACY TASK LABEL, and the CURRENT form is an ENTITY ID
#     (`{LETTER}-{table_id}-{row_id}-{version}`, `entity_id.py`).
#
# So a `ref_tag` is a PROOF TARGET, and a proof target may be an entity id, a
# prompt, a FLOW, a job ref, or a field rule. `resolve_ref_tag` reports WHICH,
# rather than assuming.
#
# `flow` ADDED 2026-09-22, AFTER `prompt`. MEASURED: two flows shared the ref_tag
# `worker_identity`, so `proof_run` could not say WHICH flow a round measured —
# one table mixing two truths. A flow needs its OWN ref_tag.
#
# WHY AFTER `prompt` AND NOT FIRST: a prompt is the thing being PROVEN; a flow is
# the METHOD that asks it. So a tag that names a prompt must resolve as a prompt,
# and only a tag that names NO prompt falls through to the flow branch. Putting
# `flow` first would let a flow key SHADOW a prompt of the same name.
REF_TAG_KINDS = ("entity_id", "prompt", "flow", "job_ref", "field_rule",
                 "unknown")


class PromptError(ValueError):
    """Raised when a prompt cannot be registered or proven."""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table if absent. Idempotent."""
    # 🔴 `prompt_registry_DDL` does not exist; the real name is
    # `PROMPT_registry_DDL` (`db_schema.py`). MEASURED 2026-09-29.
    from db_schema import PROMPT_registry_DDL
    conn.executescript(PROMPT_registry_DDL)
    conn.commit()
    return {"ok": True}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def register_prompt(
    conn: sqlite3.Connection,
    prompt_key: str,
    *,
    name: str,
    skill_id: int,
    study_id: int,
    failure_mode: str = "",
    observable: str = "",
    unit: str = "",
    threshold: str = "",
    independence: str = "",
    oracle_ref: str = "NA",
    description: str = "NA",
    template_id: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Register ONE prompt. Idempotent on `prompt_key`.

    REFUSES a prompt that cannot answer the five first-principle questions. A
    prompt with no `unit` cannot be measured, and a prompt with no `threshold`
    cannot separate pass from fail — so registering one would create a hypothesis
    nobody can test.

    `is_active` is written EXPLICITLY as 0, so the behaviour does not depend on
    the column default (SQLite cannot change a default by ALTER TABLE, so an
    existing DB keeps the old one).
    """
    key = str(prompt_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_PROMPT_KEY",
                "message": "prompt_key is required"}

    answers = {
        "failure_mode": str(failure_mode or "").strip(),
        "observable": str(observable or "").strip(),
        "unit": str(unit or "").strip(),
        "threshold": str(threshold or "").strip(),
        "independence": str(independence or "").strip(),
    }
    missing = [k for k, v in answers.items() if not v or v == "NA"]
    if missing:
        return {"ok": False, "code": "UNANSWERED_FIRST_PRINCIPLE",
                "message": ("a prompt is a hypothesis with a MEASURED UNIT; "
                            "these answers are missing: %s. Without `unit` the "
                            "prompt cannot be measured, and without `threshold` "
                            "it cannot separate pass from fail." % missing),
                "missing": missing}

    # The unit must NAME its subject — the same rule `factor_first_principle`
    # enforces for a factor. `count` alone names nothing.
    subject = fp.unit_subject(answers["unit"])
    if not subject:
        return {"ok": False, "code": "UNIT_NAMES_NOTHING",
                "message": ("unit %r names NO SUBJECT. A number without a unit "
                            "cannot be audited: '0' is a pass for a vulnerability "
                            "count and a FAIL for a test pass rate."
                            % answers["unit"])}

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT prompt_id FROM prompt_registry WHERE prompt_key=?",
        (key,)).fetchone()
    if existing:
        return {"ok": True, "prompt_id": int(existing[0]), "created": False,
                "prompt_key": key}

    cur = conn.execute(
        "INSERT INTO prompt_registry (prompt_key, name, description, skill_id, "
        "study_id, template_id, failure_mode, observable, unit, threshold, "
        "independence, oracle_ref, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (key, str(name or key).strip(), str(description or "NA").strip(),
         int(skill_id), int(study_id), template_id,
         answers["failure_mode"], answers["observable"], answers["unit"],
         answers["threshold"], answers["independence"],
         str(oracle_ref or "NA").strip()))
    if commit:
        conn.commit()
    return {"ok": True, "prompt_id": cur.lastrowid, "created": True,
            "prompt_key": key, "unit_subject": subject}


def get_prompt(conn: sqlite3.Connection, prompt_key: str) -> dict[str, Any] | None:
    try:
        row = conn.execute("SELECT * FROM prompt_registry WHERE prompt_key=?",
                           (str(prompt_key or "").strip(),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def answers_of(conn: sqlite3.Connection, prompt_key: str) -> dict[str, str]:
    """The five first-principle answers of a prompt, or {} if it has none."""
    p = get_prompt(conn, prompt_key)
    if not p:
        return {}
    return {k: str(p.get(k) or "") for k in ANSWER_COLUMNS}


def streak_target_of(conn: sqlite3.Connection, prompt_key: str,
                     *, option_count: int | None = None) -> int | None:
    """The streak target DERIVED from the prompt's `threshold`.

    MEASURED: `llm_100_run_harness.STREAK_TARGET = 110` is hard-coded as
    `options(22) * 5`. That is the PHONE case. A prompt's target must come from
    ITS OWN threshold, or every prompt would be judged by the phone's number.

    Returns None when the threshold is not a number, so a caller can tell
    "no target" from "target 0".
    """
    p = get_prompt(conn, prompt_key)
    if not p:
        return None
    try:
        thr = int(float(str(p.get("threshold") or "")))
    except (TypeError, ValueError):
        return None
    if option_count is None:
        return thr
    return int(option_count) * thr


def resolve_ref_tag(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """What does a `ref_tag` point at? Reports the KIND, never assumes.

    MEASURED 2026-09-22: the ONE existing `ref_tag` is `1.1F`, which does NOT
    resolve to a `prompt_key` — it is a `field_tdd_rule.slice_key`. So a
    single-kind assumption would have been wrong, and `activation_gate` must be
    able to tell "a prompt" from "a field rule" from "nothing".
    """
    tag = str(ref_tag or "").strip()
    if not tag:
        return {"ok": False, "kind": "unknown", "ref_tag": tag,
                "message": "ref_tag is empty"}

    # AN ENTITY ID FIRST. The user: "1.1F is old task is now is entity id". The
    # current form is `{LETTER}-{table_id}-{row_id}-{version}` (`entity_id.py`),
    # and `entity_id.verify` checks it against the registers — so an id that is
    # shaped correctly but names nothing is REFUSED, not accepted.
    #
    # THE HUMAN (2026-09-27), verbatim: "letter - table_id - row_id - version_id"
    # / "3 is old, new version for entity is 4 part" / "version is the key to
    # create mis-understand". The 3-part form is the OLD one.
    #
    # `ref_id` IS `row_id`: `version_registry.entity_ref_id` and
    # `task_entity_link.entity_ref_id` both hold the register table's OWN pk,
    # which is exactly the `row_id` part. The consumers below read `ref_id`, so
    # it is kept as the name for that value; `table_id` and `row_id` are also
    # reported so a caller can see the whole id.
    #
    # `verify` walks the WHOLE register chain, so on a DB where those tables are
    # absent it RAISES rather than reporting. A resolver must not raise: an
    # absent register means "cannot verify", which is a REFUSAL, not a crash.
    try:
        import entity_id as eid
        parsed = eid.parse(tag)
        if parsed.get("ok"):
            try:
                v = eid.verify(tag, conn=conn)
            except sqlite3.Error as e:
                return {"ok": False, "kind": "entity_id", "ref_tag": tag,
                        "message": ("ref_tag %r is SHAPED like an entity id but "
                                    "the registers cannot verify it: %s"
                                    % (tag, e))}
            if v.get("exists"):
                return {"ok": True, "kind": "entity_id", "ref_tag": tag,
                        "letter": parsed["letter"],
                        "table_id": parsed["table_id"],
                        "row_id": parsed["row_id"],
                        "ref_id": parsed["row_id"],
                        "version": parsed["version"]}
            return {"ok": False, "kind": "entity_id", "ref_tag": tag,
                    "message": ("ref_tag %r is SHAPED like an entity id but "
                                "names nothing: %s"
                                % (tag, v.get("reason")))}
    except ImportError:
        pass

    # A DIMENSION BINDING. THE HUMAN (2026-09-26): "evidence can help you to find
    # B / have all data to find the way".
    #
    # THE FORM IS `dimension_binding.{subject_kind}.{dimension_key}`, DERIVED by
    # `dimension_binding_registry.binding_ref_tag` -- the same function the
    # activation writer uses, so the two cannot disagree about the tag.
    #
    # WHY THIS BRANCH MUST EXIST, AND WHY IT MUST NOT USE `entity_id.verify`.
    #
    # MEASURED DEADLOCK (2026-09-26). `activation_gate.two_part_verdict` reads an
    # approval by `(letter, ref_id)`, and `resolve_ref_tag` returned
    # `kind='unknown'` for this form -- so the gate refused for the WRONG reason
    # ("no entity to read a 2-part verdict from") instead of the RIGHT one ("no
    # approval, no streak"). A gate that refuses for the wrong reason cannot be
    # satisfied by doing the right thing.
    #
    # The obvious fix -- resolve it as an `entity_id` -- is CIRCULAR:
    #
    #     resolve_entity requires is_active=1
    #     is_active=1 requires activation
    #     activation requires two_part_verdict
    #     two_part_verdict requires resolve_ref_tag -> entity_id
    #     entity_id requires resolve_entity -> is_active=1
    #
    # MEASURED: `dimension_binding_registry` is the ONLY lettered register with
    # 0 active rows (108 of 108 are `is_active=0`), so `resolve_entity` returns
    # None for EVERY binding and the id can never verify.
    #
    # THE RESOLUTION IS THE ONE `prompt_registry` ALREADY USES: a PROMPT resolves
    # by `prompt_key` with NO `is_active` check (measured: the query is
    # `SELECT prompt_id FROM prompt_registry WHERE prompt_key=?`). A ref_tag
    # names WHAT IS BEING MEASURED, not what has been PROVEN -- the proof is the
    # streak, and the gate reads it separately. So this branch resolves the
    # binding by its COMPOSITE KEY and reports the letter + ref_id, and the
    # `is_active` question stays where it belongs: in the gate.
    if _table_exists(conn, "dimension_binding_registry"):
        parts = tag.split(".")
        if len(parts) == 3 and parts[0] == "dimension_binding":
            row = conn.execute(
                "SELECT binding_id FROM dimension_binding_registry "
                "WHERE subject_kind=? AND dimension_key=?",
                (parts[1], parts[2])).fetchone()
            if row:
                return {"ok": True, "kind": "entity_id", "ref_tag": tag,
                        "letter": "Y", "ref_id": int(row[0]),
                        "version": 1,
                        "note": ("a dimension binding; resolved by its "
                                 "COMPOSITE KEY, not by `is_active`, because "
                                 "`is_active` is what the gate is deciding")}
            return {"ok": False, "kind": "dimension_binding", "ref_tag": tag,
                    "message": ("ref_tag %r names no binding: no "
                                "dimension_binding_registry row for "
                                "subject_kind=%r dimension_key=%r"
                                % (tag, parts[1], parts[2]))}

    if _table_exists(conn, "prompt_registry"):
        row = conn.execute("SELECT prompt_id FROM prompt_registry WHERE "
                           "prompt_key=?", (tag,)).fetchone()
        if row:
            return {"ok": True, "kind": "prompt", "ref_tag": tag,
                    "prompt_id": int(row[0])}
    # A FLOW. MEASURED (2026-09-22): two flows shared the ref_tag
    # `worker_identity`, so `proof_run` could not say WHICH flow a round
    # measured. A flow key is NOT a prompt key, so it falls through to here.
    #
    # The flow's ORACLE is its FINAL step's prompt's oracle — a flow is a METHOD,
    # and the method's pass/fail is decided by the question it ends on. That rule
    # lives in `llm_100_run_harness.oracle_for`, which reads the step.
    if _table_exists(conn, "workflow_registry"):
        row = conn.execute("SELECT workflow_id FROM workflow_registry WHERE "
                           "workflow_key=?", (tag,)).fetchone()
        if row:
            return {"ok": True, "kind": "flow", "ref_tag": tag,
                    "workflow_id": int(row[0])}
    # A LEGACY JOB REF. MEASURED: `1.1F` is a `task_ssot` value with
    # `dim_key='job_ref'`, and `llm_100_run` records it as `entity_type='field'`,
    # `entity_name='phone'`. Kept so the 2615 existing rounds still resolve.
    if _table_exists(conn, "task_ssot"):
        row = conn.execute(
            "SELECT task_id FROM task_ssot WHERE dim_key='job_ref' AND "
            "value_text=? ORDER BY task_id LIMIT 1", (tag,)).fetchone()
        if row:
            return {"ok": True, "kind": "job_ref", "ref_tag": tag,
                    "task_id": int(row[0]),
                    "note": "a LEGACY task label; the current form is an entity id"}
    # A FIELD RULE. MEASURED: `field_tdd_rule.slice_key` holds field NAMES
    # (`phone`, `name`, `region`), so this matches a ref_tag that IS a field name.
    if _table_exists(conn, "field_tdd_rule"):
        row = conn.execute("SELECT id FROM field_tdd_rule WHERE slice_key=? "
                           "ORDER BY id LIMIT 1", (tag,)).fetchone()
        if row:
            return {"ok": True, "kind": "field_rule", "ref_tag": tag,
                    "rule_id": int(row[0])}
    return {"ok": False, "kind": "unknown", "ref_tag": tag,
            "message": ("ref_tag %r resolves to NO entity id, NO prompt, NO "
                        "flow, NO job ref and NO field rule — a streak that "
                        "belongs to nothing cannot prove anything" % tag)}


def attribute_failure(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """WHICH DIMENSION caused the failures. The missing link for the formula.

    The user: "as prompt is generatot by formula, so we can have enough detail to
    help for improve formula design".

    A prompt is built from `prompt_wording` dimensions. So a failure must be
    attributable to a DIMENSION, or the formula can only be improved from
    opinion. `llm_100_run` has `failure_reason` but no `dim_key`; this joins the
    failures to the prompt's own dimensions so the attribution is possible.

    REPORTS ONLY. It never edits a prompt or a wording.
    """
    tag = str(ref_tag or "").strip()
    out: dict[str, Any] = {"ref_tag": tag, "by_dimension": {}, "failures": 0,
                           "rounds": 0, "attributable": False}
    if not _table_exists(conn, "llm_100_run"):
        return out
    row = conn.execute("SELECT COUNT(*), SUM(win) FROM llm_100_run WHERE "
                       "ref_tag=?", (tag,)).fetchone()
    out["rounds"] = int(row[0] or 0)
    out["failures"] = int(out["rounds"] - int(row[1] or 0))

    # The prompt's own dimensions, if the ref_tag IS a prompt.
    resolved = resolve_ref_tag(conn, tag)
    if resolved.get("kind") != "prompt":
        out["reason"] = ("ref_tag %r is a %s, not a prompt, so it has no "
                         "`prompt_wording` dimensions to attribute to"
                         % (tag, resolved.get("kind")))
        return out
    if not _table_exists(conn, "prompt_wording"):
        out["reason"] = "prompt_wording is absent"
        return out
    dims = [dict(r) for r in conn.execute(
        "SELECT dim_key, wording_id FROM prompt_wording WHERE prompt_id=? "
        "ORDER BY dim_key", (int(resolved["prompt_id"]),))]
    out["dimensions"] = dims
    # A `dim_key` column on `llm_100_run` is what makes the attribution exact.
    cols = {c[1] for c in conn.execute("PRAGMA table_info(llm_100_run)")}
    if "dim_key" not in cols:
        out["reason"] = ("llm_100_run has no `dim_key`, so a failure cannot be "
                         "attributed to a dimension — the formula can only be "
                         "improved from opinion")
        return out
    for r in conn.execute(
            "SELECT dim_key, COUNT(*) n FROM llm_100_run WHERE ref_tag=? "
            "AND win=0 GROUP BY dim_key ORDER BY n DESC", (tag,)):
        out["by_dimension"][str(r["dim_key"])] = int(r["n"])
    out["attributable"] = True
    return out


def unproven(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every prompt that is NOT active. A prompt nobody proved."""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT prompt_id, prompt_key, is_active, unit, threshold "
            "FROM prompt_registry WHERE is_active=0 ORDER BY prompt_id")]
    except sqlite3.OperationalError:
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prompt register")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--unproven", action="store_true")
    ap.add_argument("--resolve", metavar="REF_TAG")
    ap.add_argument("--attribute", metavar="REF_TAG")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.resolve:
            print(resolve_ref_tag(conn, args.resolve))
        if args.attribute:
            print(attribute_failure(conn, args.attribute))
        if args.unproven:
            rows = unproven(conn)
            print("=== prompts that are NOT active (n=%d) ===" % len(rows))
            for r in rows[:20]:
                print("  %-4s %-60s unit=%s"
                      % (r["prompt_id"], r["prompt_key"][:58], r["unit"]))
        if args.list or not (args.resolve or args.attribute or args.unproven):
            for r in conn.execute("SELECT prompt_id, prompt_key, is_active, "
                                  "unit, threshold FROM prompt_registry "
                                  "ORDER BY prompt_id"):
                print("  %-4s %-60s active=%s unit=%s thr=%s"
                      % (r["prompt_id"], r["prompt_key"][:58], r["is_active"],
                         r["unit"], r["threshold"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
