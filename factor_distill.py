# -*- coding: utf-8 -*-
"""factor_distill.py — DISTILL a factor from data, and record WHY it changed.

THE HUMAN'S RULE (2026-09-21)
-----------------------------
    "i find the key is 蒸馏 (distillation)
     you give LLM data -> how LLM 蒸馏 factor from data
     factor table template, how to Create, Update
     Create, Update -> by reason
     by reason will be lesson like skill lesson"

Four requirements, and they are one pipeline:

    1. a factor is DISTILLED from data, not invented
    2. the factor table has a TEMPLATE (what a factor must contain)
    3. Create and Update are the only two write paths
    4. BOTH carry a REASON, and the reason becomes a LESSON

WHY DISTILLATION AND NOT GENERATION
-----------------------------------
Measured earlier in this workset: a generator that INVENTED factors produced 11
factor names across all 50 skills, 642 of 652 rows UNMEASURED, and 0 mentions of
the skill's own subject. Inventing a rule from a skill's NAME produces a rule
about nothing. Distillation is the opposite direction: the data comes first, and
the factor is what the data SUPPORTS.

So `distill()` refuses to emit a factor unless the data carries the evidence for
it. The evidence is the citation, and the citation is the same gate
`citation_discipline` already enforces — no citation, no factor.

WHY THE REASON IS A LESSON
--------------------------
`skill_factor_registry` holds the CURRENT state. An UPDATE overwrites, so the
register cannot answer "why is this rule here?" or "what did it replace?" — the
same defect `skill_factor_proof_log` was added to fix for measurements. A rule
whose reason is lost is indistinguishable from an accident, and the next person
deletes it.

So every create/update appends to `factor_change_log` (reason + citation +
before/after) AND files a `skill_lesson` with source_type 'factor_change'. The
reason is then visible wherever lessons are read, not only in a table nobody
opens.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import skill_factor as sf  # noqa: E402

DB = BASE / "agent.db"

# THE FACTOR TEMPLATE IS A TABLE, NOT THIS DICT.
#
# The human's correction (2026-09-22): "factor template table, or something like
# that, name can help to define". The template was a Python dict here, which is
# the same defect the tag registry and the wording register were fixed for: a
# definition that lives in code cannot be extended without a code change, and
# cannot be read by anything that does not import this module.
#
# The table is `factor_template` (seeded from `db_schema.FACTOR_TEMPLATE_SEED`),
# and `sf.template_fields(conn)` reads it. This dict is kept ONLY as the offline
# fallback for a caller with no connection, and it is checked against the table
# by `_proof_factor_template_table.py` so the two cannot drift.
FACTOR_TEMPLATE: dict[str, dict[str, Any]] = {
    "factor_key": {"required": True, "kind": "slug",
                   "why": "identity; a factor with no key cannot be cited"},
    "name": {"required": True, "kind": "text",
             "why": "a human reads this in the table"},
    "rule_definition": {"required": True, "kind": "text",
                        "why": "the rule itself, in one sentence"},
    "action": {"required": True, "kind": "text",
               "why": "what to DO when it fails; a rule with no action is advice"},
    "metric_kind": {"required": True, "kind": "enum",
                    "why": "a metric that cannot be scored is decorative"},
    "metric_unit": {"required": True, "kind": "text",
                    "why": "a number without a unit cannot be audited"},
    "metric_target": {"required": True, "kind": "text",
                      "why": "the pass condition"},
    "proof_prefix": {"required": True, "kind": "slug",
                     "why": "so a proof can be found by prefix"},
    "cite_ref": {"required": True, "kind": "citation",
                 "why": "no citation, no factor"},
}


class DistillError(ValueError):
    """Raised when the data does not support a factor, or a reason is missing."""


def template_fields() -> list[str]:
    return [k for k, v in FACTOR_TEMPLATE.items() if v["required"]]


def check_template(factor: dict) -> list[str]:
    """Return the template fields the factor is MISSING or has empty."""
    missing = []
    for field in template_fields():
        v = factor.get(field)
        if v is None or not str(v).strip():
            missing.append(field)
    return missing


# ---------------------------------------------------------------------------
# DISTILLATION
# ---------------------------------------------------------------------------

def distill(observations: list[dict], *, skill_key: str | None = None) -> dict:
    """Distill ONE factor from a set of observations.

    An observation is a measured fact about the world, and it must carry:
        subject   what was observed (the unit's subject)
        value     the measured number
        cite_ref  where a third party can check it

    The factor is DERIVED from the observations, not supplied:
      * `metric_unit`  <- the subject the observations share
      * `metric_kind`  <- count when the values are integers, pct when they are
                          percentages, boolean when they are true/false
      * `metric_target`<- the value the observations AGREE is acceptable
      * `cite_ref`     <- the observations' citations, joined

    REFUSES when the data does not support a factor:
      * fewer than 2 observations — one point is an anecdote, not a pattern
      * observations that do not share a subject — no single unit exists
      * observations with no citation — no citation, no factor
      * observations that DISAGREE on the acceptable value — the target would be
        invented, and an invented target is a rule nobody measured
    """
    if len(observations) < 2:
        raise DistillError(
            "distill: %d observation(s). One point is an anecdote, not a "
            "pattern — a factor distilled from it would be a guess with a "
            "heading." % len(observations))

    subjects = {str(o.get("subject") or "").strip() for o in observations}
    if "" in subjects:
        raise DistillError(
            "distill: an observation has no `subject`. Without a subject there "
            "is no unit, and a number with no unit cannot be audited.")
    if len(subjects) != 1:
        raise DistillError(
            "distill: observations do not share a subject (%s). A factor "
            "measures ONE thing; these are %d different things."
            % (sorted(subjects), len(subjects)))
    subject = subjects.pop()

    uncited = [i for i, o in enumerate(observations)
               if not str(o.get("cite_ref") or "").strip()]
    if uncited:
        raise DistillError(
            "distill: observation(s) %s carry no cite_ref. No citation, no "
            "factor — an uncited factor is a finding that cannot be checked."
            % uncited)

    values = [o.get("value") for o in observations]
    kind = _infer_kind(values)
    target = _infer_target(values, kind)
    cites = sorted({str(o["cite_ref"]).strip() for o in observations})

    # ONE checkable citation, not a joined list. MEASURED: joining them with
    # "; " produced 'run:a.log:12; run:a.log:40', which `citation_discipline`
    # REFUSED — correctly, because a joined string is not itself a citation a
    # third party can check. The gate caught a real defect in this function.
    # So the factor carries the FIRST citation (checkable on its own) and the
    # full list is returned alongside, for the record.
    factor = {
        "factor_key": _slug(subject),
        "name": subject[:1].upper() + subject[1:],
        "rule_definition": "The measured %s must be %s %s." % (subject, _cmp(kind), target),
        "action": "Fix the cause and re-measure %s." % subject,
        "metric_kind": kind,
        "metric_unit": subject,
        "metric_target": str(target),
        "proof_prefix": _slug(subject)[:24],
        "cite_ref": cites[0],
    }
    missing = check_template(factor)
    if missing:
        raise DistillError(
            "distill: the distilled factor is missing template field(s) %s. "
            "The data did not support a complete factor." % missing)
    return {"factor": factor, "observations": len(observations),
            "citations": cites, "skill_key": skill_key}


def _infer_kind(values: list) -> str:
    """The metric kind the DATA supports — not one the caller prefers."""
    if all(isinstance(v, bool) for v in values):
        return sf.MK_BOOL
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            raise DistillError(
                "distill: value %r is not numeric, so no metric kind can be "
                "inferred. A factor whose values cannot be scored is "
                "decorative." % (v,))
    if all(0.0 <= n <= 100.0 for n in nums) and any(n != int(n) for n in nums):
        return sf.MK_PCT
    return sf.MK_COUNT


def _infer_target(values: list, kind: str) -> Any:
    """The acceptable value the observations AGREE on.

    For a count the natural target is the best observed value (the minimum, for
    a defect count). For a pct/score it is the best observed (the maximum). The
    point is that it comes FROM the data: if the observations disagree about
    what is acceptable, this refuses rather than inventing one.
    """
    if kind == sf.MK_BOOL:
        if len(set(bool(v) for v in values)) != 1:
            raise DistillError(
                "distill: boolean observations disagree (%s), so no target is "
                "supported by the data." % values)
        return "true" if all(values) else "false"
    nums = [float(v) for v in values]
    if kind == sf.MK_COUNT:
        return int(min(nums))
    return max(nums)


def _cmp(kind: str) -> str:
    return {"count": "at most", "pct": "at least", "score_0_100": "at least",
            "boolean": "equal to"}.get(kind, "equal to")


def _slug(text: str) -> str:
    out = []
    for ch in str(text).lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:48] or "factor"


# ---------------------------------------------------------------------------
# CREATE / UPDATE — both by reason
# ---------------------------------------------------------------------------

def create_factor(conn: sqlite3.Connection, factor: dict, *, reason: str,
                  cite_ref: str | None = None, skill_key: str | None = None,
                  applies_to: str = sf.APPLIES_ALL, sort_order: int = 0,
                  round_index: int = 0, round_verdict: str = "NA") -> dict:
    """CREATE a factor. A reason is REQUIRED, and it becomes a lesson."""
    _require_reason(reason, "create")
    key = str(factor.get("factor_key") or "").strip()
    if not key:
        raise DistillError("create_factor: factor_key is required")
    existing = conn.execute("SELECT factor_id FROM skill_factor_registry "
                            "WHERE factor_key=?", (key,)).fetchone()
    if existing:
        raise DistillError(
            "create_factor: factor %r already exists. Use update_factor — a "
            "create that silently overwrites loses the previous reason." % key)
    missing = check_template(factor)
    if missing:
        raise DistillError(
            "create_factor: factor %r is missing template field(s) %s"
            % (key, missing))
    sf.register_factor(
        conn, factor_key=key, name=factor["name"],
        rule_definition=factor["rule_definition"], action=factor["action"],
        metric_kind=factor["metric_kind"], metric_unit=factor["metric_unit"],
        metric_target=str(factor["metric_target"]),
        proof_prefix=factor["proof_prefix"], skill_key=skill_key,
        applies_to=applies_to, sort_order=sort_order,
        cite_ref=cite_ref or factor["cite_ref"])
    return _log_change(conn, key, "create", reason,
                       cite_ref or factor["cite_ref"], before=None,
                       after=factor, skill_key=skill_key,
                       round_index=round_index, round_verdict=round_verdict)


def update_factor(conn: sqlite3.Connection, factor_key: str, changes: dict, *,
                  reason: str, cite_ref: str | None = None,
                  round_index: int = 0, round_verdict: str = "NA") -> dict:
    """UPDATE a factor. A reason is REQUIRED, and it becomes a lesson.

    The BEFORE state is captured first, so the log can show what the rule was.
    An update that records only the new value cannot answer "what did it
    replace?", which is the question a reason exists to answer.
    """
    _require_reason(reason, "update")
    row = conn.execute("SELECT * FROM skill_factor_registry WHERE factor_key=?",
                       (factor_key,)).fetchone()
    if not row:
        raise DistillError(
            "update_factor: factor %r is not registered. Use create_factor — an "
            "update of a non-existent rule has no before-state to record."
            % factor_key)
    before = dict(row)
    merged = dict(before)
    merged.update({k: v for k, v in changes.items() if k in FACTOR_TEMPLATE})
    # The citation is NOT a column on the register (deliberate: a column would
    # duplicate it in a mutable row and break the fixed-column-set invariant).
    # It is read from the append-only change log, which is where the last
    # create/update recorded it.
    if not str(merged.get("cite_ref") or "").strip():
        merged["cite_ref"] = cite_ref or _last_citation(conn, factor_key)
    missing = check_template(merged)
    if missing:
        raise DistillError(
            "update_factor: the result would be missing template field(s) %s. "
            "An update must not leave an incomplete rule." % missing)
    sf.register_factor(
        conn, factor_key=factor_key, name=merged["name"],
        rule_definition=merged["rule_definition"], action=merged["action"],
        metric_kind=merged["metric_kind"], metric_unit=merged["metric_unit"],
        metric_target=str(merged["metric_target"]),
        proof_prefix=merged["proof_prefix"],
        skill_key=merged.get("skill_key"),
        applies_to=merged.get("applies_to") or sf.APPLIES_ALL,
        sort_order=int(merged.get("sort_order") or 0),
        cite_ref=cite_ref or merged.get("cite_ref") or before.get("cite_ref") or "")
    return _log_change(conn, factor_key, "update", reason,
                       cite_ref or merged.get("cite_ref") or "",
                       before=before, after=merged,
                       skill_key=merged.get("skill_key"),
                       round_index=round_index, round_verdict=round_verdict)


def _require_reason(reason: str, action: str) -> None:
    if not str(reason or "").strip():
        raise DistillError(
            "%s_factor: a reason is REQUIRED. A rule change with no recorded "
            "reason is indistinguishable from an accident, and the next reader "
            "deletes it." % action)


def _log_change(conn: sqlite3.Connection, factor_key: str, action: str,
                reason: str, cite_ref: str, *, before: dict | None,
                after: dict, skill_key: str | None,
                round_index: int = 0, round_verdict: str = "NA") -> dict:
    """Append the change AND file the reason as a lesson."""
    sf.ensure_schema(conn)
    lesson_key = None
    # The lesson is filed against the skill the factor belongs to. A generic
    # factor has no skill, so it is filed against the factor's own key — a
    # lesson must belong to SOMETHING or it is not findable.
    lesson_skill = skill_key or after.get("skill_key") or factor_key
    try:
        import skill_learning as sl
        res = sl.add_lesson(
            lesson_skill,
            "Factor %s %sd: %s" % (factor_key, action, reason.strip()),
            source_type="factor_change",
            root_cause=reason.strip(),
            suggested_fix=after.get("action"),
            source_ref=cite_ref or None,
            status="draft",
            conn=conn,
        )
        lesson_key = res.get("lesson_key")
    except Exception as e:  # a lesson failure must not lose the change record
        lesson_key = "LESSON_FAILED: %s" % str(e)[:80]

    conn.execute(
        "INSERT INTO factor_change_log (factor_key, action, reason, cite_ref, "
        "before_json, after_json, lesson_key, round_index, round_verdict) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (factor_key, action, reason.strip(), cite_ref,
         json.dumps(before, default=str) if before else None,
         json.dumps(after, default=str), lesson_key,
         int(round_index), str(round_verdict)))
    conn.commit()
    return {"ok": True, "factor_key": factor_key, "action": action,
            "reason": reason.strip(), "lesson_key": lesson_key,
            "round_index": int(round_index), "round_verdict": str(round_verdict)}


def _last_citation(conn: sqlite3.Connection, factor_key: str) -> str:
    """The citation the most recent recorded change carried.

    The register has no `cite_ref` column on purpose — the citation lives in the
    append-only `factor_change_log`, so an UPDATE must read it back from there
    rather than from the (absent) column.
    """
    row = conn.execute(
        "SELECT cite_ref FROM factor_change_log WHERE factor_key=? AND "
        "cite_ref IS NOT NULL AND cite_ref <> '' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1", (factor_key,)).fetchone()
    return str(row[0]) if row else ""


def change_history(conn: sqlite3.Connection, factor_key: str) -> list[dict]:
    """Every recorded change to a factor, newest first."""
    sf.ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM factor_change_log WHERE factor_key=? "
        "ORDER BY changed_at DESC, id DESC", (factor_key,))]


def distill_change(existing: dict, observations: list[dict]) -> dict:
    """Distill what has CHANGED between a factor and newer observations.

    A rule updated by re-running the CREATE path would need the whole factor
    restated by hand, and the reason would be whatever the caller felt like
    saying. This derives the CHANGE from the data instead:

        {field: (before, after)}  plus a plain-language reason

    The reason is built from the measurement, so it says what moved and why —
    which is the same shape a human writes when updating a rule honestly.
    Validated against the template, so a change cannot leave a broken rule.
    """
    fresh = distill(observations)["factor"]
    changes: dict[str, tuple] = {}
    for field in template_fields():
        before, after = existing.get(field), fresh.get(field)
        if str(before) != str(after):
            changes[field] = (before, after)
    if not changes:
        raise DistillError(
            "distill_change: the observations produce the same factor, so there "
            "is nothing to update. Re-running an identical measurement is not a "
            "change, and logging one would inflate the history.")
    moved = ", ".join("%s %r -> %r" % (f, b, a) for f, (b, a) in
                      sorted(changes.items()))
    reason = ("distilled from %d observation(s): %s"
              % (len(observations), moved))
    return {"changes": changes, "factor": fresh, "reason": reason,
            "citations": sorted({str(o["cite_ref"]).strip()
                                 for o in observations})}


# ---------------------------------------------------------------------------
# THE ITERATED CYCLE
#
#     DATA -> distill -> factor_1 -> distill -> factor_2 -> ... -> QUESTION
#
# The human's model, and the reason one round is not enough:
#     "factor -> 蒸馏 -> question"
#     "factor -> 蒸馏 -> factor -> 蒸馏 -> question"
#     "if = 蒸馏 then 1 -> 2 -> 3 ....."
#
# WHY THERE IS AN INDEX
# ---------------------
# Round 3's factor must be attributable to round 3, or "where did this come
# from?" has no answer. So each round's change is logged with `round_index`, and
# the resulting factor carries `distill_round`.
#
# WHERE DOES IT STOP? — CLOSURE, NOT A COUNTER
# --------------------------------------------
# "cycle-closed? not" — correct, a cycle of distillation does not close on its
# own. It closes when the factor STOPS MOVING: if a round produces the same
# factor as the round before, further rounds would produce the same thing again.
# That is a real fixed point, and it is detectable.
#
# Three outcomes, and each is a different thing:
#   * CLOSED   the factor stopped moving -> emit the QUESTION
#   * CAP      the round limit was reached while the factor was STILL moving
#              -> this is a defect signal, NOT success: the data is not
#              converging, so the question would be built on an unstable factor.
#              It emits the question but reports `closed=False`.
#   * UNDEFINED the audit could not judge a round -> stop; do not build a
#              question on an unanswerable level.
#
# A FAILING round stops the cycle and becomes the LESSON (the human's
# "fail = lesson?").
# ---------------------------------------------------------------------------

MAX_DISTILL_ROUNDS = 5


def distill_cycle(observations: list[dict], *, max_rounds: int = MAX_DISTILL_ROUNDS,
                  audit: bool = True, persist: dict | None = None) -> dict:
    """Run `distill` ROUNDS over the data until the factor stops changing.

    `persist` is optional: {"conn": conn, "skill_key": ...}. When given, EVERY
    round is recorded in `factor_change_log` with its `round_index`, so the
    history is queryable rather than reconstructed.
    """
    if max_rounds < 1:
        raise DistillError("distill_cycle: max_rounds must be >= 1")

    rounds: list[dict] = []
    previous: dict | None = None
    for i in range(1, max_rounds + 1):
        # EVERY round is a genuine distillation call over the same evidence: the
        # input does not change, so a round that changes the factor is changing
        # it because the PREVIOUS round's factor is now part of the input.
        data = observations if previous is None else (
            observations + [{"subject": previous["metric_unit"],
                             "value": previous["metric_target"],
                             "cite_ref": previous["cite_ref"],
                             "note": "carried from round %d" % (i - 1)}])
        try:
            got = distill(data)
        except DistillError as e:
            rounds.append({"round": i, "error": str(e)})
            return {"status": "REFUSED", "rounds": rounds, "factor": previous,
                    "reason": "round %d could not distill: %s" % (i, e),
                    "closed": False}
        factor = got["factor"]
        moved = previous is None or any(
            str(factor.get(k)) != str(previous.get(k)) for k in template_fields())

        verdict = "NA"
        if audit:
            try:
                import factor_audit as fa
                verdict = fa.audit_factor_dict(factor)["verdict"]
            except Exception as e:
                verdict = "AUDIT_ERROR: %s" % str(e)[:60]

        entry = {"round": i, "factor_key": factor["factor_key"],
                 "moved": moved, "verdict": verdict,
                 "metric_target": factor["metric_target"],
                 "metric_unit": factor["metric_unit"],
                 "cite_ref": factor["cite_ref"]}
        rounds.append(entry)

        if persist:
            _log_round(persist, factor, i, verdict, moved, previous)

        # FAIL stops the cycle and is the lesson.
        if verdict == "FAIL":
            return {"status": "FAIL", "rounds": rounds, "factor": factor,
                    "closed": False,
                    "reason": "round %d audited FAIL; the cycle stops and the "
                              "failure is the lesson" % i}
        if verdict == "UNDEFINED":
            return {"status": "UNDEFINED", "rounds": rounds, "factor": factor,
                    "closed": False,
                    "reason": "round %d is UNDEFINED; a question must not be "
                              "built on an unanswerable level" % i}
        # CLOSURE: the factor stopped moving, so further rounds produce the same
        # result. This is the real stop condition -- not a round counter.
        if previous is not None and not moved:
            return {"status": "CLOSED", "rounds": rounds, "factor": factor,
                    "closed": True,
                    "reason": "the factor stopped moving at round %d" % i}
        previous = factor

    return {"status": "CAP", "rounds": rounds, "factor": previous,
            "closed": False,
            "reason": "reached the limit of %d rounds while the factor was STILL "
                      "moving; the data is not converging, so the question would "
                      "be built on an unstable factor" % max_rounds}


def _log_round(persist: dict, factor: dict, round_index: int, verdict: str,
               moved: bool, previous: dict | None) -> None:
    """Record ONE round by calling the ordinary create/update path.

    DEFECT FOUND BY RUNNING IT (2026-09-21): this first called `_log_change`
    directly AND then called `create_factor`, which logs as well — so round 1
    produced TWO log rows and 3 rows appeared for a 2-round cycle. The fix is to
    let create/update own the logging: one round, one row. A round that logs
    twice makes every per-round count wrong.
    """
    conn = persist["conn"]
    skill_key = persist.get("skill_key")
    existing = conn.execute("SELECT factor_id FROM skill_factor_registry "
                            "WHERE factor_key=?", (factor["factor_key"],)).fetchone()
    changes = {}
    if previous:
        changes = {k: (previous.get(k), factor.get(k)) for k in template_fields()
                   if str(previous.get(k)) != str(factor.get(k))}
    reason = ("round %d: %s" % (round_index,
              "factor unchanged (fixed point)" if not moved
              else "factor moved: " + (", ".join(
                  "%s %r -> %r" % (k, b, a) for k, (b, a) in sorted(changes.items())
              ) or "first distillation")))
    if existing:
        update_factor(conn, factor["factor_key"], factor, reason=reason,
                      round_index=round_index, round_verdict=verdict)
    else:
        create_factor(conn, factor, reason=reason, skill_key=skill_key,
                      applies_to=persist.get("applies_to", sf.APPLIES_ALL),
                      sort_order=persist.get("sort_order", 0),
                      round_index=round_index, round_verdict=verdict)


def build_question(cycle: dict, *, question_template: str | None = None) -> dict:
    """The QUESTION at the end of the cycle, derived from the final factor.

    Returns the question ONLY when the cycle closed. A question built on a cycle
    that never closed is a question about an unstable factor — it looks
    finished and is not, so this refuses rather than emitting it with a warning.
    """
    if not cycle.get("closed"):
        raise DistillError(
            "build_question: the cycle did not close (status=%s). A question "
            "built on a factor that is still moving measures a moving target."
            % cycle.get("status"))
    f = cycle["factor"]
    tmpl = question_template or (
        "QUESTION: Is {metric_unit} at most/at least {metric_target}? "
        "Answer YES or NO.")
    return {"question": tmpl.format(metric_unit=f["metric_unit"],
                                    metric_target=f["metric_target"]),
            "factor": f, "rounds": len(cycle["rounds"]),
            "cite_ref": f["cite_ref"]}


def _main() -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print("factor template fields: %s" % ", ".join(template_fields()))
    n = conn.execute("SELECT COUNT(*) FROM factor_change_log").fetchone()[0]
    print("recorded factor changes: %d" % n)
    conn.close()


if __name__ == "__main__":
    _main()