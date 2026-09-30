# -*- coding: utf-8 -*-
"""independent_review.py — the EXECUTABLE form of the independent-review rules.

WHY THIS FILE EXISTS
--------------------
This session had three external workers (DeepSeek / 豆包 / Gemini) answer ONE
question with 7 / 16 / 8 modules. Every one of those was a judgement; none was
built on a route list. They could not be combined, because there was nothing to
combine them AGAINST.

The lesson was not "get more workers". It was:

    THE DISAGREEMENT WAS THE VALUABLE OUTPUT, because it forced a measurement.

And the same session produced the counter-example that makes the rule necessary:
THREE consecutive detectors that each returned `{}` and each time I reported it
as success. A broken detector and a working one had the SAME observable. So an
empty result from a worker cannot be accepted without a positive control.

A rule in a table is not a gate. These rules live here as PURE functions, and
both the proof (`_proof_independent_review.py`) and the TDD runner probe
(`skill_tdd_runner._probe_ir`) import from here. One copy of the rule, two
consumers — otherwise the proof certifies a rule the gate no longer runs.

Pure: no I/O, no DB, no network. Callers supply the state.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

ADOPT = "adopt"
MEASURE = "measure"
CLARIFY = "clarify"
CONTROL = "control"
REFUSE = "refuse"

# The four parts of a statement (see problem_statement.py). A claim that cannot
# be reduced to these cannot be adjudicated — you can only agree with the words.
OBSERVABLE_PARTS = ("target", "expected", "actual", "observable")


class UndiscriminatedDisagreement(RuntimeError):
    """Raised instead of permitting a verdict adopted from a vote."""


class NoPositiveControl(RuntimeError):
    """Raised instead of accepting an empty result with no control."""


# ---------------------------------------------------------------------------
# R1 / R2 — agreement is not evidence; disagreement triggers a measurement
# ---------------------------------------------------------------------------


def normalize(verdicts: Iterable[Mapping[str, Any]] | None) -> list[dict]:
    """Coerce worker verdicts into a comparable list.

    Accepts dicts `{worker, claim, cite_ref}` or bare strings (a bare string is
    a claim with no worker and no citation — which is exactly what it is, and
    it will be refused for having no citation rather than silently upgraded).
    """
    out: list[dict] = []
    for i, v in enumerate(verdicts or []):
        if isinstance(v, Mapping):
            out.append({
                "worker": str(v.get("worker") or "worker%d" % (i + 1)),
                "claim": v.get("claim"),
                "cite_ref": v.get("cite_ref"),
            })
        elif v is not None:
            out.append({"worker": "worker%d" % (i + 1),
                        "claim": v, "cite_ref": None})
    return out


def _key(claim: Any) -> str:
    """A stable comparison key for a claim.

    A mapping claim is compared on its SORTED items so two workers describing
    the same partition in a different key order are seen as agreeing. Without
    this, `{a:1,b:2}` and `{b:2,a:1}` look like a disagreement and the review
    fires on a formatting difference.
    """
    if isinstance(claim, Mapping):
        return repr(tuple(sorted((str(k), _key(v)) for k, v in claim.items())))
    if isinstance(claim, (list, tuple)):
        return repr([_key(x) for x in claim])
    return repr(claim)


def positions(verdicts: Iterable[Mapping[str, Any]] | None) -> dict[str, list[str]]:
    """Group worker names by the position they hold.

    Returns `{claim_key: [worker, ...]}` with MORE THAN ONE position only when
    the workers genuinely disagree.
    """
    groups: dict[str, list[str]] = {}
    for v in normalize(verdicts):
        groups.setdefault(_key(v["claim"]), []).append(v["worker"])
    return groups


def disagreement_count(verdicts: Iterable[Mapping[str, Any]] | None) -> int:
    """How many distinct positions were held. 0 workers -> 0, 1 position -> 1."""
    return len(positions(verdicts))


def is_unanimous(verdicts: Iterable[Mapping[str, Any]] | None) -> bool:
    """True when every worker holds ONE position (and there is at least one)."""
    n = disagreement_count(verdicts)
    return n == 1


def agreement_ratio(verdicts: Iterable[Mapping[str, Any]] | None) -> float | None:
    """Share of workers holding the majority position, or None if no workers.

    REPORTED, NEVER ACTED ON. A high ratio is not a reason to adopt: if all
    three workers read the same wrong document, the ratio is 1.0 and the
    conclusion is wrong with full confidence.
    """
    groups = positions(verdicts)
    total = sum(len(v) for v in groups.values())
    if not total:
        return None
    return round(max(len(v) for v in groups.values()) / total, 4)


def disagreeing_workers(verdicts: Iterable[Mapping[str, Any]] | None) -> dict[str, list[str]]:
    """The positions, from the SECOND-largest group onward.

    These are the workers whose answer the majority would overwrite. They are
    returned, not discarded, because one of them may be the only correct one.
    """
    groups = positions(verdicts)
    if len(groups) <= 1:
        return {}
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    return dict(ordered[1:])


# ---------------------------------------------------------------------------
# R3 — a claim must reduce to a checkable observable
# ---------------------------------------------------------------------------


def reduce_to_observable(claim: Any) -> dict:
    """Reduce a claim to target / expected / actual / observable.

    Returns `{complete, parts, missing}`.

    TWO ACCEPTED SHAPES, and the distinction is measured, not aesthetic:
      * a FULL 4-part statement -> complete. If a caller has started naming the
        parts it must finish: `{target, expected}` with no `actual` is not a
        statement, it is half of one.
      * a STRUCTURED claim with at least one non-empty entry ->
        `{"modules": 7}` names an observable (module count) and gives a value a
        third party can go and count. This is the shape the three workers
        ACTUALLY returned this session ("7 modules" / "16 modules" / "8
        modules"). An earlier version of this function demanded the 4-part form
        and therefore classified the real case as uncheckable, sending it to
        CLARIFY instead of MEASURE — the code was too strict, so the code was
        fixed rather than the fixture.
      * PROSE is not a claim. `"it is too slow"` names no target and no value,
        so it can only be agreed with. That is the case R3 exists to catch.
    """
    if not isinstance(claim, Mapping):
        return {"complete": False, "parts": {},
                "missing": list(OBSERVABLE_PARTS)}

    present = [k for k in OBSERVABLE_PARTS if k in claim]
    if present:
        # a partial statement is not a statement
        parts = {k: claim.get(k) for k in OBSERVABLE_PARTS}
        missing = [k for k, v in parts.items()
                   if v is None or (isinstance(v, str) and not v.strip())
                   or (isinstance(v, (list, tuple, dict)) and not v)]
        return {"complete": not missing, "parts": parts, "missing": missing}

    # structured claim: the keys name the observable, the values are the values
    values = {k: v for k, v in claim.items()
              if v is not None and not (isinstance(v, str) and not v.strip())}
    return {"complete": bool(values),
            "parts": {"target": sorted(map(str, values.keys())),
                      "expected": values},
            "missing": [] if values else list(OBSERVABLE_PARTS)}


def all_reducible(verdicts: Iterable[Mapping[str, Any]] | None) -> bool:
    """True when EVERY worker's claim reduces to a checkable observable."""
    vs = normalize(verdicts)
    return bool(vs) and all(reduce_to_observable(v["claim"])["complete"]
                            for v in vs)


# ---------------------------------------------------------------------------
# the discriminating measurement
# ---------------------------------------------------------------------------


def is_discriminated(verdicts: Iterable[Mapping[str, Any]] | None,
                     measurement: Mapping[str, Any] | None) -> bool:
    """True when a measurement EXISTS that separates the held positions.

    A measurement counts only when it is:
      * present,
      * CARRIES A CITATION (an uncited measurement is an opinion), and
      * names the positions it separates, covering MORE THAN ONE of them —
        a measurement that addresses a single position cannot discriminate,
        it can only confirm.
    """
    if not isinstance(measurement, Mapping):
        return False
    if not str(measurement.get("cite_ref") or "").strip():
        return False
    separated = measurement.get("separates")
    if separated is None:
        return False
    if isinstance(separated, str):
        separated = [separated]
    try:
        covered = {_key(s) for s in separated}
    except TypeError:
        return False
    held = set(positions(verdicts))
    return len(covered & held) > 1


# ---------------------------------------------------------------------------
# R4 — an empty result needs a POSITIVE CONTROL
# ---------------------------------------------------------------------------


def assert_positive_control(result: Mapping[str, Any] | None) -> None:
    """Refuse an empty result that has no positive control.

    MEASURED REASON: three consecutive broken detectors in this session each
    returned `{}`, and the empty result was reported as "no problem found". The
    broken state and the honest state were the SAME observable. A control is
    the only thing that separates them: if the control is non-empty, the
    detector demonstrably can find something, so an empty result is meaningful.
    """
    res = result if isinstance(result, Mapping) else {}
    found = res.get("found")
    empty = found is None or (hasattr(found, "__len__") and len(found) == 0)
    if not empty:
        return
    control = res.get("positive_control")
    ctl_len = len(control) if hasattr(control, "__len__") else (
        0 if control is None else 1)
    if ctl_len == 0:
        raise NoPositiveControl(
            "empty result with no positive control: a broken detector and a "
            "clean result have the same output, so this cannot be accepted")


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def assert_may_adopt(verdicts: Iterable[Mapping[str, Any]] | None, *,
                     measurement: Mapping[str, Any] | None = None,
                     cite_ref: str | None = None) -> dict:
    """THE HARD RULE. Raise instead of permitting a vote to decide.

    Checked in this order, and each check is UNCONDITIONAL:

      1. every verdict carries its OWN citation (R1). This used to live INSIDE
         `if not cite_ref`, i.e. passing any top-level cite_ref skipped the
         per-worker citation check entirely — a gate that could be bypassed by
         supplying an unrelated string. Found by `_proof_ir_caller_wired.py`
         section F, which adopted a UNANIMOUS uncited review.
      2. a discriminating measurement exists (R2), for UNANIMOUS reviews too.
         Agreement is not evidence: if the workers all read the same wrong
         document, `agreement_ratio` is 1.0 and adopting on it is a confident
         error. An earlier version only refused when the workers DISAGREED, so
         unanimity slipped through.
      3. the adoption itself carries a citation.
    """
    vs = normalize(verdicts)
    if not vs:
        raise UndiscriminatedDisagreement("no verdicts to adopt")

    uncited = [v["worker"] for v in vs
               if not str(v.get("cite_ref") or "").strip()]
    if uncited:
        raise UndiscriminatedDisagreement(
            "no citation for %s: agreement is not evidence" % ", ".join(uncited))

    n_pos = disagreement_count(vs)
    if not is_discriminated(vs, measurement):
        if n_pos > 1:
            raise UndiscriminatedDisagreement(
                "independent workers hold %d positions and no discriminating "
                "measurement exists; run a measurement, do NOT vote"
                % n_pos)
        raise UndiscriminatedDisagreement(
            "unanimous (%d workers, 1 position) with no discriminating "
            "measurement; agreement is not evidence" % len(vs))

    if not str(cite_ref or "").strip():
        raise UndiscriminatedDisagreement(
            "adoption carries no citation for the decision itself")
    return {"ok": True, "positions": n_pos, "workers": len(vs),
            "discriminated": True}


# ---------------------------------------------------------------------------
# adjudication — returns a NEXT ACTION, never a winner
# ---------------------------------------------------------------------------


def adjudicate(verdicts: Iterable[Mapping[str, Any]] | None, *,
               measurement: Mapping[str, Any] | None = None,
               empty_result: Mapping[str, Any] | None = None) -> dict:
    """Decide WHAT TO DO NEXT. Deliberately has no `winner` field.

    The value that makes this different from a vote is that there is no code
    path that picks the majority position. `selected` is always None; a
    measurement decides, or nothing does.
    """
    vs = normalize(verdicts)
    out: dict[str, Any] = {
        "workers": len(vs),
        "positions": disagreement_count(vs),
        "agreement_ratio": agreement_ratio(vs),
        "disagreeing": disagreeing_workers(vs),
        "selected": None,          # NEVER a worker's claim
        "next_action": None,
        "reason": "",
    }

    if empty_result is not None:
        try:
            assert_positive_control(empty_result)
        except NoPositiveControl as e:
            out["next_action"] = CONTROL
            out["reason"] = str(e)
            return out

    if not vs:
        out["next_action"] = CLARIFY
        out["reason"] = "no verdicts supplied"
        return out

    if not all_reducible(vs):
        missing = sorted({m for v in vs
                          for m in reduce_to_observable(v["claim"])["missing"]})
        out["next_action"] = CLARIFY
        out["reason"] = ("claim is not checkable by a third party; missing %s"
                         % ", ".join(missing))
        return out

    if not is_discriminated(vs, measurement):
        # covers BOTH unanimity and disagreement: neither is adoptable on
        # agreement alone (R1), so the next action is always to measure.
        out["next_action"] = MEASURE
        out["reason"] = (
            "workers hold %d position(s) with no discriminating measurement; "
            "agreement is not evidence" % disagreement_count(vs))
        return out

    out["next_action"] = ADOPT
    out["reason"] = "a cited measurement discriminates the positions"
    return out


def explain(res: Mapping[str, Any]) -> str:
    """One-screen rendering of an adjudication."""
    lines = [
        "independent review: %s worker(s), %s position(s), agreement=%s"
        % (res.get("workers"), res.get("positions"),
           res.get("agreement_ratio")),
    ]
    dis = res.get("disagreeing") or {}
    if dis:
        lines.append("  minority position(s):")
        for k, workers in dis.items():
            lines.append("    %s  <- %s" % (k, ", ".join(workers)))
    else:
        lines.append("  no minority position (unanimous)")
    lines.append("  selected: %s  (never a worker's claim)" % res.get("selected"))
    lines.append("  NEXT_ACTION: %s" % res.get("next_action"))
    lines.append("  reason: %s" % res.get("reason"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# R5 — a heuristic rule must be measured in BOTH directions
# ---------------------------------------------------------------------------

def measure_rule(rule, sample: Sequence[Any]) -> dict:
    """Run a predicate over a sample and report BOTH failure directions.

    MEASURED REASON: this session produced a token-overlap rule that matched 25
    routes (20 wrong) and a substring rule that matched 5. A rule that matches
    everything and a rule that matches nothing are equally useless, and equally
    confident. One number cannot show which failure you have.
    """
    hits = [x for x in sample if rule(x)]
    return {
        "n": len(sample),
        "matched": len(hits),
        "match_rate": (round(len(hits) / len(sample), 4) if sample else None),
        "matches_nothing": bool(sample) and not hits,
        "matches_everything": bool(sample) and len(hits) == len(sample),
        "hits": hits,
    }


__all__ = [
    "ADOPT", "MEASURE", "CLARIFY", "CONTROL", "REFUSE",
    "OBSERVABLE_PARTS",
    "UndiscriminatedDisagreement", "NoPositiveControl",
    "normalize", "positions", "disagreement_count", "is_unanimous",
    "agreement_ratio", "disagreeing_workers",
    "reduce_to_observable", "all_reducible",
    "is_discriminated",
    "assert_positive_control",
    "assert_may_adopt",
    "adjudicate", "explain", "measure_rule",
]