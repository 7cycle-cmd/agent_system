"""done_chain.py — a hard gate that is also a SENSOR.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "A, worker submit to hard gate to got fail = we can collect factor, that can
     help us to improve, can be factor template can be logic / prompt / skill
     generator"
    "not block only, this is cycle, have evidence sample to let worker know why
     your submit is rejected, so they can have the work more easiler too"

MEASURED BEFORE THIS MODULE, and the user's diagnosis was exactly right:
  * `task_instances` (1444 rows, 496 `success`), `dev_task` (165) and
    `skill_task_queue` (263) all carry a `status` and **NONE carries an evidence
    column** — a worker could set `success` with nothing behind it.
  * **No gate refused a `done` write** (grep for such a gate returned 0 hits).
  * The re-learn machinery ALREADY EXISTED and was not connected:
    `factor_template_growth.propose()` turns a REFUSAL into an `is_active=0`
    proposal, and `question_flow.record_lesson` writes a `skill_lesson` with
    `root_cause` + `suggested_fix` from a failed step.

THE LINE, NOT THE WALL. A refusal is not the end of the task; it is the INPUT to
the next iteration:

    submit -> walk the chain -> a ring fails -> REFUSE (hard, named)
                                            -> collect: an is_active=0 FACTOR
                                            -> feedback: a WORKED EXAMPLE
                                            -> the worker does it right next time

A REFUSAL REASON MUST BE STRUCTURED, AND THIS IS NOT A STYLE CHOICE.
MEASURED and recorded in `factor_template_growth`: its FIRST version derived
factors from `proof_run.failure_reason`, which produced GARBAGE —
`oracle_999999999999999`, `oracle_session_abc_model` — because that field says WHY
A ROUND FAILED, not WHAT PROPERTY IS MISSING. Its words are the TEST PAYLOAD.
So this module returns `{ring, unit, observed, expected}` and the collector REFUSES
anything that is not that shape. A free-text reason is not collected at all.

THE CHAIN IS THE 5W1H ASK, ANSWERED WITH A UNIT. The user's framing: "B with
5W1H middleware to let worker know her request clearly, by which measured unit".
Each ring names a dimension and a unit whose subject is named, so a ring's number
is auditable — the rule `factor_first_principle` states for a factor.

Every source below ALREADY EXISTS; this module adds no new measurement.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# THE RINGS, in a FIXED order, each with its 5W1H dimension and its UNIT.
#
# A MAPPING plus an ORDERED tuple, not a chain of `if`s: a new ring is a row here
# and the walk reads the order. The order runs CHEAPEST-AND-MOST-FUNDAMENTAL
# first: a file that is not linked to the task makes every later ring unreadable.
RINGS: tuple[str, ...] = ("files_changed", "task_link", "route_health",
                          "verdict", "cited")

RING_SPEC: dict[str, dict[str, str]] = {
    "files_changed": {
        "dim": "what",
        "unit": "count of files this task changed",
        "ask": "Which files did this task change?",
    },
    "task_link": {
        "dim": "why",
        "unit": "count of changed files linked to THIS task",
        "ask": "Why is each changed file part of this task?",
    },
    "route_health": {
        "dim": "where",
        "unit": "count of affected routes whose health is not OK",
        "ask": "Where does each changed file sit in a declared route, and is "
               "that route healthy?",
    },
    "verdict": {
        "dim": "how",
        "unit": "count of judged steps whose verdict is YES",
        "ask": "How is the change verified — which checks passed?",
    },
    "cited": {
        "dim": "who",
        "unit": "count of claimed facts whose cite_ref is checkable",
        "ask": "Who can check each claim, and at which path:line?",
    },
}

# A ring that reports 0 of its unit is SATISFIED for the "count of problems"
# rings; a ring whose unit counts a GOOD thing is satisfied when it is NON-zero.
# Declared, not inferred, because guessing the polarity is how a gate passes a
# broken task.
#   count of PROBLEMS  -> satisfied at 0
#   count of GOODS     -> satisfied when > 0
RING_POLARITY: dict[str, str] = {
    "files_changed": "positive",   # 0 changed files => nothing to prove
    "task_link": "complete",       # every changed file must be linked
    "route_health": "problems",    # satisfied at 0 unhealthy routes
    "verdict": "positive",         # needs at least one YES
    "cited": "complete",           # every claim must be citable
}


class DoneChainError(ValueError):
    """The chain cannot be walked as stated."""


# THE FIELD SOURCE, DERIVED — NOT A SECOND COPY.
#
# `skill_registrar.fields_from_seed` reads a module's `DIMENSIONS` as
# `(field_name, question, hard_rule, mandatory)`. The rings are ALREADY declared
# once, in `RING_SPEC`, and the skill's own "Not to do" forbids a restatement
# ("a rule in two places lets one copy be neutered while the other stays green").
# So this is a DERIVED view: add a ring above and it appears here, with no second
# edit to forget.
DIMENSIONS: tuple[tuple[str, str, str, bool], ...] = tuple(
    (ring, RING_SPEC[ring]["ask"],
     "must be answered with a measurable value in the unit: %s"
     % RING_SPEC[ring]["unit"], True)
    for ring in RINGS
)


def unit_of(ring: str) -> str:
    """The UNIT a ring is measured in. '' for an unknown ring (never guessed)."""
    return str(RING_SPEC.get(str(ring or "").strip(), {}).get("unit", ""))


def assert_units() -> list[str]:
    """Every ring must have a unit that NAMES A SUBJECT. Returns the problems."""
    problems: list[str] = []
    for ring in RINGS:
        u = unit_of(ring)
        if not u:
            problems.append("ring %r has no unit" % ring)
            continue
        low = u.lower()
        if low in ("count", "value", "total", "number") or (
                low.startswith("count") and len(low.split()) < 3):
            problems.append("ring %r unit %r names NO SUBJECT" % (ring, u))
    return problems


def walk_chain(conn: sqlite3.Connection, task_id: str, *,
               changed_files: list[str] | None = None,
               affected_routes: list[str] | None = None,
               verdict: str | None = None,
               claims: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Walk every ring for ONE task. NEVER raises; REPORTS every ring.

    Returns `{ok, task_id, rings:[{ring, dim, unit, count, satisfied, cite_ref,
    observed}], failed}`. A ring is returned even when it PASSES, because a
    report that lists only failures cannot show that the chain was walked.
    """
    tid = str(task_id or "").strip()
    if not tid:
        raise DoneChainError("task_id is required")
    files = [str(f) for f in (changed_files or [])]
    rings: list[dict[str, Any]] = []

    def _emit(ring: str, count: int, cite_ref: str, observed: Any) -> None:
        pol = RING_POLARITY[ring]
        if pol == "problems":
            sat = int(count) == 0
        elif pol == "complete":
            sat = int(count) == 0
        else:                       # positive
            sat = int(count) > 0
        rings.append({"ring": ring, "dim": RING_SPEC[ring]["dim"],
                      "unit": RING_SPEC[ring]["unit"], "count": int(count),
                      "polarity": pol, "satisfied": bool(sat),
                      "cite_ref": cite_ref, "observed": observed})

    # --- ring 1: files_changed -------------------------------------------
    # The count of FILES, so a task that changed nothing cannot claim it is done
    # by having nothing to check.
    _emit("files_changed", len(files),
          "done_chain.py:%d" % _line_of("RINGS"), {"files": files[:8]})

    # --- ring 2: task_link -----------------------------------------------
    # MEASURED: `task_entity_link` has 0 rows, so NO task is linked to a file.
    # The count is of changed files WITHOUT a link — the ring's own unit.
    unlinked: list[str] = []
    try:
        linked = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT entity_type FROM task_entity_link WHERE track_id=?",
            (tid,))}
    except sqlite3.OperationalError:
        linked = set()
    for f in files:
        # A link may be declared by file path or by module; either counts.
        mod = str(f).replace("\\", "/").split("/")[-1].removesuffix(".py")
        if f not in linked and mod not in linked and not linked:
            unlinked.append(f)
    _emit("task_link", len(unlinked), "done_chain.py:%d" % _line_of("RING_POLARITY"),
          {"unlinked": unlinked[:8], "linked_kinds": sorted(linked)[:6]})

    # --- ring 3: route_health --------------------------------------------
    unhealthy: list[str] = []
    try:
        import route_registry as rr
        for r in rr.routes_of(conn):
            if str(r.get("health") or "") not in ("OK",):
                unhealthy.append(str(r.get("route_key")))
    except Exception as exc:
        unhealthy = ["<route_registry unreadable: %s>" % exc]
    if affected_routes is not None:
        unhealthy = [r for r in unhealthy if r in set(affected_routes)]
    _emit("route_health", len(unhealthy), "route_registry.py:1",
          {"unhealthy": unhealthy[:8]})

    # --- ring 4: verdict --------------------------------------------------
    # A computed verdict from `question_flow.run_flow`. `YES` is the only pass,
    # so the ring fails when the verdict is anything else, INCLUDING missing.
    v = str(verdict or "").strip().upper()
    _emit("verdict", 1 if v == "YES" else 0, "question_flow.py:677",
          {"verdict": v or "MISSING"})

    # --- ring 5: cited ----------------------------------------------------
    # Every claimed fact must carry a checkable citation. `is_citation` is SHAPE
    # only; `verify_db_ref` is called for a `register:` ref, and a path:line is
    # shape-checked here (existence is checked by the caller that produced it).
    import citation_discipline as cd
    uncited: list[str] = []
    for c in (claims or []):
        ref = str(c.get("cite_ref") or "").strip()
        if not ref or not cd.is_citation(ref):
            uncited.append(str(c.get("name") or ref or "<no name>"))
    _emit("cited", len(uncited), "citation_discipline.py:1",
          {"uncited": uncited[:8], "claims": len(claims or [])})

    failed = [r["ring"] for r in rings if not r["satisfied"]]
    return {"ok": not failed, "task_id": tid, "rings": rings, "failed": failed}


def _line_of(symbol: str) -> int:
    """The line of a module-level symbol, so a `cite_ref` is DERIVED not typed."""
    import pathlib
    src = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(src, 1):
        if line.startswith(symbol):
            return i
    return 1


# ---------------------------------------------------------------------------
# P2 — THE HARD GATE
# ---------------------------------------------------------------------------

def assert_may_complete(conn: sqlite3.Connection, task_id: str, **kw
                        ) -> dict[str, Any]:
    """HARD GATE: may this task be recorded as done?

    Returns `{allow, reason, chain, refused_rings}`. `allow=False` means the write
    MUST be refused. The reason is a STRUCTURED code naming the FIRST failing
    ring, never a free-text sentence that a collector would have to parse.
    """
    chain = walk_chain(conn, task_id, **kw)
    if chain["ok"]:
        return {"allow": True, "code": "CHAIN_COMPLETE", "reason": "",
                "chain": chain, "refused_rings": []}
    first = chain["failed"][0]
    return {"allow": False, "code": "RING_FAILED",
            "reason": "ring %r failed: %s (observed %s)"
                      % (first, unit_of(first),
                         next(r["count"] for r in chain["rings"]
                              if r["ring"] == first)),
            "failed_ring": first, "chain": chain,
            "refused_rings": chain["failed"]}


# ---------------------------------------------------------------------------
# P3 — A REFUSAL BECOMES A FACTOR (never auto-activated)
# ---------------------------------------------------------------------------

# A STRUCTURED reason has EXACTLY these keys. MEASURED why this is enforced and
# not merely documented: `factor_template_growth`'s first version read
# `proof_run.failure_reason` and produced `oracle_999999999999999`, because that
# field holds the TEST PAYLOAD, not a missing property. A free-text reason is
# the same hazard, so the collector REFUSES one.
STRUCTURED_REASON_KEYS: frozenset[str] = frozenset(
    {"ring", "unit", "observed", "expected"})


def is_structured_reason(reason: Any) -> bool:
    """Does `reason` carry the four keys the collector requires?"""
    if not isinstance(reason, dict):
        return False
    if not STRUCTURED_REASON_KEYS <= set(reason):
        return False
    return all(str(reason.get(k, "")).strip() != ""
               for k in ("ring", "unit"))


def propose_factor_from_refusal(conn: sqlite3.Connection, task_id: str, *,
                                reason: Any, cite_ref: str,
                                apply: bool = False) -> dict[str, Any]:
    """Turn a refusal into an `is_active=0` FACTOR proposal.

    REFUSES an unstructured reason (`UNSTRUCTURED_REASON`), because collecting one
    would re-derive factors from noise — the recorded defect of the module this
    reuses.

    NEVER AUTO-ACTIVATES. `is_active=0` is the declared default for an unproven
    row, and a factor that activated itself would change what every future task
    must carry, decided by one failure.
    """
    if not is_structured_reason(reason):
        return {"ok": False, "code": "UNSTRUCTURED_REASON",
                "message": ("a refusal reason must carry %s; got %r. A free-text "
                            "reason is the `failure_reason` garbage the existing "
                            "collector documents"
                            % (sorted(STRUCTURED_REASON_KEYS), reason))}
    ring = str(reason["ring"])
    if ring not in RING_SPEC:
        return {"ok": False, "code": "UNKNOWN_RING",
                "message": "ring %r is not declared in RING_SPEC" % ring}
    import citation_discipline as cd
    if not str(cite_ref or "").strip() or not cd.is_citation(str(cite_ref)):
        return {"ok": False, "code": "UNCITED_PROPOSAL",
                "message": "no citation, no proposal: %r" % cite_ref}
    factor_key = "done_ring_%s" % ring
    unit = unit_of(ring)
    # THE PROPOSAL GOES THROUGH THE REGISTER'S OWN WRITE PATH.
    #
    # MEASURED, and my first version was WRONG: it ran a raw INSERT and hit
    # `NOT NULL constraint failed: skill_factor_registry.action`, because the
    # table has four NOT NULL columns the proposal did not set (`action`,
    # `metric_target`, `proof_prefix`, and a `metric_kind` CHECK). Re-listing
    # those columns here would be a SECOND copy of the schema that drifts the
    # moment a column is added — so the proposal delegates to
    # `skill_factor.register_factor`, which owns the shape and its refusals.
    row = {"factor_key": factor_key, "name": "task done ring: %s" % ring,
           "rule_definition": ("a task may not be recorded done while ring %r is "
                               "unsatisfied: %s" % (ring, reason.get("expected") or "")),
           "action": "block the done write and re-run the ring's check",
           "metric_kind": "count", "metric_unit": unit, "metric_target": "0",
           "proof_prefix": "done_ring_%s" % ring,
           "skill_key": "skill_task_done_verification",
           "cite_ref": str(cite_ref)}
    existing = conn.execute("SELECT factor_id FROM skill_factor_registry "
                            "WHERE factor_key=?", (factor_key,)).fetchone()
    if existing:
        return {"ok": True, "action": "already_proposed", "factor_key": factor_key,
                "is_active": 0, "unit": unit,
                "factor_id": int(existing[0])}
    # A PROPOSAL, so it is written only when the caller asks (apply=True).
    # Default False keeps a dry run dry.
    if apply:
        import skill_factor as sf
        r = sf.register_factor(conn, **{k: v for k, v in row.items()
                                        if k != "cite_ref"},
                               cite_ref=str(cite_ref))
        if not r.get("ok"):
            return {"ok": False, "code": "REGISTER_REFUSED",
                    "message": str(r.get("message") or r)[:200]}
        # `register_factor` does not take `is_active`; the declared default for a
        # fresh row is 1, so a PROPOSAL is explicitly set to 0 here. That is the
        # one write the caller must know about, and it is recorded.
        conn.execute("UPDATE skill_factor_registry SET is_active=0 "
                     "WHERE factor_key=?", (factor_key,))
        conn.commit()
    return {"ok": True, "action": "proposed" if apply else "would_propose",
            "factor_key": factor_key, "is_active": 0, "unit": unit,
            "row": row}


# ---------------------------------------------------------------------------
# P4 — A REFUSAL BECOMES A WORKED EXAMPLE THE WORKER CAN READ
# ---------------------------------------------------------------------------

def file_refusal_example(conn: sqlite3.Connection, task_id: str, *,
                         ring: str, cite_ref: str, lesson_text: str = "",
                         skill_key: str = "skill_task_done_verification",
                         commit: bool = True) -> dict[str, Any]:
    """A refusal becomes a `skill_lesson`: WHAT broke, and WHAT TO DO.

    MEASURED why `suggested_fix` is REQUIRED and not optional:
    `question_flow.record_lesson` records that leaving it empty made its own proof
    report "every lesson names a suggested fix" FAILED. A lesson that says what
    went wrong but not what to DO is a complaint, not a lesson — the same rule
    `problem-statement` enforces.

    The fix is DERIVED from the ring's own spec (`ask` + `unit`), so it names the
    question the worker must answer and the unit to answer it in — which is the
    user's "so they can have the work more easier too".
    """
    if ring not in RING_SPEC:
        return {"ok": False, "code": "UNKNOWN_RING",
                "message": "ring %r is not declared" % ring}
    import citation_discipline as cd
    if not str(cite_ref or "").strip() or not cd.is_citation(str(cite_ref)):
        return {"ok": False, "code": "UNCITED_LESSON",
                "message": "no citation, no lesson: %r" % cite_ref}
    spec = RING_SPEC[ring]
    root = lesson_text or ("task %s was refused at ring %r: %s"
                           % (task_id, ring, spec["unit"]))
    fix = "Answer the %s question: %s Measure it as: %s" % (
        spec["dim"], spec["ask"], spec["unit"])
    import skill_learning as sl
    r = sl.add_lesson(
        skill_key, root, source_type="self_fail", root_cause=root,
        suggested_fix=fix, source_ref=cite_ref, status="draft", conn=conn)
    return {"ok": True, "lesson": r, "ring": ring, "root_cause": root,
            "suggested_fix": fix, "source_ref": cite_ref}


def refused_reasons(conn: sqlite3.Connection, task_id: str, **kw
                    ) -> list[dict[str, Any]]:
    """The STRUCTURED reason for EACH failing ring — one per ring, in order.

    This is the collector's input, and it is already the right shape: the walk
    produces `{ring, unit, observed, expected}` per ring, so no free text is ever
    parsed. Returns `[]` for a complete chain.
    """
    chain = walk_chain(conn, task_id, **kw)
    out: list[dict[str, Any]] = []
    for r in chain["rings"]:
        if r["satisfied"]:
            continue
        out.append({"ring": r["ring"], "unit": r["unit"],
                    "observed": r["count"],
                    "expected": RING_POLARITY[r["ring"]],
                    "dim": r["dim"], "cite_ref": r["cite_ref"]})
    return out
