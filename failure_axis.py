# -*- coding: utf-8 -*-
"""failure_axis.py — make a failure CLASSIFIABLE, and make the class DERIVABLE.

WHY (all measured 2026-09-21, none assumed)
------------------------------------------
Three independent sides of this system each record a failure outcome, and every
one of them has exactly TWO values — one bit:

    TDD      skill_contract_tdd_case.kind       pass 57 / hard_fail 48
    Ontology audit_trace.verdict                0 = 2394 / 1 = 98
    Code     field_tdd / pair_qc fail_class     business_defect / transient_execution

So "more DIFFERENT failures" is not blocked by case count. A generator can only
emit INTO values that exist, and one bit of failure outcome cannot say whether a
run failed because it could not be parsed, because it was wrong, or because it
could not be traced. Adding cases would only add MORE OF THE SAME TWO CLASSES.

THE SIGNALS ARE COUNTED BEFORE THE CLASSES ARE NAMED
----------------------------------------------------
A class no signal can separate is decorative, so the classes below are derived
from what this codebase can actually OBSERVE at a failure, not proposed first:

    retryable    (bool)      an I/O/timeout failure that may succeed on retry
    slots_ok     (bool)      every template slot had a value
    parses       (bool)      the answer could be read as a verdict at all
    cited        (bool)      the finding carries a checkable reference
    rule_key     (str|None)  which registered rule fired (None = no rule covers it)
    meaning_ok   (bool)      the answer means the right thing

A class is a PATTERN over those signals. `classify_failure` counts the matching
classes and REFUSES on zero or on more than one, so the class is derived rather
than typed. That refusal is the point: `rule_key` present with `meaning_ok`
False genuinely matches two classes and needs a human decision, and a
classification that silently picks one is the same defect as a silently empty
slot.

TWO COPY-PASTES THIS REPLACES
-----------------------------
`field_tdd` and `pair_qc` each define their own FAIL_BUSINESS / FAIL_TRANSIENT.
Two copies of a taxonomy drift; the registered axis is the single copy.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# A dedicated skill_key: failure classification applies to member_card fields,
# to mouse-spot runs and to ontology checks alike. Scoping it to one module
# would make it unreachable from the others.
SKILL_KEY = "failure_classification"

# (value_key, definition, description) — the definition must NAME AN OBSERVABLE,
# because a class whose definition is a mood cannot be measured.
CLASS_VALUES: tuple[tuple[str, str, str], ...] = (
    ("business_defect",
     "A registered domain rule fired (rule_key is set). Shape and traceability fine.",
     "the rule that was violated is nameable, so the fix is in the rule or the data"),
    ("transient_execution",
     "An I/O, timeout or process failure occurred; retryable=True.",
     "not a defect in the work; it is a defect in the run"),
    ("structural_defect",
     "Shape is wrong: a slot had no value (slots_ok=False) or the answer could "
     "not be parsed (parses=False).",
     "measured origin: '{{subject}}' sent verbatim, and 'SERVE ?' from an empty "
     "object slot. Both were UNCLASSIFIABLE before this axis existed."),
    ("semantic_defect",
     "Shape parses and slots are filled, but the MEANING is wrong (meaning_ok=False).",
     "measured origin: the relation question answered NO to every real YES with "
     "balanced accuracy 50.0 — nothing was malformed, the judgement was wrong"),
    ("provenance_defect",
     "The finding cannot be traced to a source: cited=False.",
     "no citation, no finding — this class is what citation_discipline refuses"),
)

# The signal pattern per class, as a PREDICATE over the signals.
#
# FIRST ATTEMPT (retracted): these were written as "a signal not named does not
# constrain me", which is TRUE individually and USELESS in aggregate — an
# under-constrained predicate set matched 4 of 5 classes for an ordinary
# retryable failure. `_proof_failure_axis.py` section B caught it. An
# over-matching classifier that always refuses is indistinguishable from a
# classifier that does not work.
#
# The defect was algebraic, not a bug: `retryable=True` alone must MEAN
# transient, which requires the other classes to actively NOT match. So each
# predicate now names what DISQUALIFIES it as well as what qualifies it.
def _is_transient(s: dict[str, Any]) -> bool:
    return s.get("retryable") is True


def _is_structural(s: dict[str, Any]) -> bool:
    # A malformed shape dominates: text that will not parse cannot be judged,
    # so a parse failure is the CAUSE and anything downstream is a symptom.
    return s.get("slots_ok") is False or s.get("parses") is False


def _is_provenance(s: dict[str, Any]) -> bool:
    return s.get("cited") is False


def _is_business(s: dict[str, Any]) -> bool:
    """A registered rule can be CITED as the thing that was violated.

    The `rule_key` alone decides this. The first version ALSO required
    `meaning_ok is not False`, which made the taxonomy unable to express the most
    ordinary case of all: a rule that fired and found a violation. That is
    `rule_key` set AND `meaning_ok` False, and it matched NO class.

    Measured while classifying `member_id` (op=match on a well-formed string):
    there IS a rule to cite, so the class is business_defect. Whether the meaning
    was right is a CONSEQUENCE of the rule, not an independent condition.
    """
    return bool(s.get("rule_key"))


def _is_semantic(s: dict[str, Any]) -> bool:
    """Wrong meaning with NO rule to cite — so no rule explains it.

    Mutually exclusive with `_is_business` BY CONSTRUCTION (one requires a
    rule_key, the other requires none), so co-matching cannot happen and the
    share-tier ambiguity below introduces no false refusal.
    """
    return s.get("meaning_ok") is False and not s.get("rule_key")


# (value_key, predicate, tier). LOWER tier wins — i.e. tier 1 is the HIGHEST
# priority. Two classes on the SAME tier that both match are genuinely ambiguous
# and are REFUSED rather than ordered; ordering them would silently pick a
# cause, which is the defect this axis exists to remove.
#
# NUMBERING DEFECT FIXED (measured by _proof_failure_axis.py section B2): the
# first version gave transient tier 1 while documenting that structural must
# dominate it. "Lower wins" + "transient=1" made the LEAST specific class win
# every tie. Tiers are now ordered by how much the class NARROWS the cause.
#
# Tier 1 — a definite, visible defect. Each is independently observable and
#   neither is a symptom of the other, so they are co-equal and co-matching is
#   refused rather than ordered.
# Tier 2 — requires judging MEANING, so it is less narrow than tier 1 but more
#   informative than "the run was flaky".
# Tier 3 — the most generic outcome: the run failed and may simply be retried.
#
# business/semantic share tier 2: they are mutually exclusive by construction
# (`_is_business` requires a rule_key, `_is_semantic` requires none), so
# co-matching cannot happen and no false refusal is introduced.
CLASS_RULES: tuple[tuple[str, Any, int], ...] = (
    ("structural_defect", _is_structural, 1),
    ("provenance_defect", _is_provenance, 1),
    ("business_defect", _is_business, 2),
    ("semantic_defect", _is_semantic, 2),
    ("transient_execution", _is_transient, 3),
)

# A citeable class definition per value. `link_failure_class` REQUIRES one, so a
# class written into another row can always be traced back to the definition it
# was chosen by. Measured need: I set fail_class="structural_defect" on
# `member_id` because a value was REQUIRED and none was derived — and there was
# nothing to cite that would have shown it was wrong.
CLASS_CITE: dict[str, str] = {
    "business_defect": "register:wording_registry:21",
    "transient_execution": "register:wording_registry:22",
    "structural_defect": "register:wording_registry:23",
    "semantic_defect": "register:wording_registry:24",
    "provenance_defect": "register:wording_registry:25",
}

PATTERNS: dict[str, dict[str, Any]] = {
    "transient_execution": {"retryable": True},
    "structural_defect": {"slots_ok": False, "parses": False},
    "provenance_defect": {"cited": False},
    "semantic_defect": {"meaning_ok": False, "rule_key": None},
    "business_defect": {"rule_key": "set"},
}

SIGNALS = ("retryable", "slots_ok", "parses", "cited", "rule_key", "meaning_ok")

# What an ABSENT signal means. A test fixture that defaults every signal to
# False asserts that the finding is simultaneously retryable, malformed,
# uncited and wrong-meaning — which is why the first version of the proof had
# every class "unreachable". Benign defaults, not False.
BENIGN: dict[str, Any] = {
    "retryable": False, "slots_ok": True, "parses": True, "cited": True,
    "rule_key": None, "meaning_ok": True,
}


class FailureAxisError(ValueError):
    """Raised when a failure cannot be classified, or the axis is malformed."""


def ensure_axis(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Register `failure_class` in the LIVE dimension register."""
    import prompt_generator as pg

    pg.ensure_tables(conn)
    added: list[str] = []
    updated: list[str] = []
    for value_key, definition, desc in CLASS_VALUES:
        # The definition is stored as the wording template so it is readable
        # through the same `registry()` the composer uses. It has no slot, which
        # `assert_template_slots` accepts (it only refuses UNRESOLVABLE braces).
        pg.assert_template_slots(definition, where="%s.%s" % (SKILL_KEY, value_key))
        before = conn.execute(
            "SELECT wording_id FROM wording_registry w "
            "JOIN component_registry k ON k.skill_id = w.skill_id "
            "WHERE k.skill_key = ? AND w.dim_key = 'failure_class' "
            "AND w.wording_key = ?", (SKILL_KEY, value_key)).fetchone()
        pg.upsert_dimension(
            conn, skill_key=SKILL_KEY, dim_key="failure_class",
            value_key=value_key, value_text=definition, dim_name="failure_class",
            description=desc, commit=False)
        (updated if before else added).append(value_key)
    if commit:
        conn.commit()
    return {"ok": True, "skill_key": SKILL_KEY, "added": added,
            "updated": updated, "added_count": len(added),
            "updated_count": len(updated)}


def registered_classes(conn: sqlite3.Connection) -> list[str]:
    import prompt_generator as pg

    reg = pg.registry(conn, SKILL_KEY)
    return sorted(reg.get("failure_class", {}).get("values", {}))


def _matches(signals: dict[str, Any], pattern: dict[str, Any]) -> bool:
    """Kept for introspection/reporting only — `classify_failure` uses predicates.

    `"set"` is used for `rule_key` because a rule key is a string, not a bool:
    asking `rule_key is True` would never match.
    """
    for key, want in pattern.items():
        got = signals.get(key)
        if want == "set":
            if not got:
                return False
        elif got is not want:
            return False
    return True


def classify_failure(signals: dict[str, Any],
                     *, registered: list[str] | None = None) -> dict[str, Any]:
    """Derive the failure class from OBSERVABLE signals. Never guesses.

    Returns {"ok", "failure_class", "matched", "why", "tier", "ambiguous"}.
    `ok=False` with `failure_class=None` on ambiguity or on no match — an
    unclassifiable failure is a REPORTED outcome, not a default.

    Absent signals take their BENIGN value, so a caller supplies only what it
    can actually observe and cannot accidentally assert a defect it did not see.
    """
    unknown = sorted(set(signals) - set(SIGNALS))
    if unknown:
        raise FailureAxisError(
            "unknown signal(s) %s; known=%s. An unrecognised signal would be "
            "silently ignored, which is how a class gets decided by the "
            "signals that happened to be present." % (unknown, list(SIGNALS))
        )
    full = dict(BENIGN)
    full.update(signals)

    hits = [(c, t) for c, pred, t in CLASS_RULES
            if (registered is None or c in registered) and pred(full)]
    if not hits:
        return {"ok": False, "failure_class": None, "matched": [], "tier": None,
                "ambiguous": False,
                "why": "NO class matched; the failure is unclassifiable with the "
                       "signals available (not 'unknown class' — this is a gap "
                       "in the taxonomy or in the signals)"}
    top = min(t for _, t in hits)
    winners = [c for c, t in hits if t == top]
    if len(winners) > 1:
        return {"ok": False, "failure_class": None, "matched": winners,
                "tier": top, "ambiguous": True,
                "why": "AMBIGUOUS at tier %d: %s both match and neither is a "
                       "symptom of the other. Ordering them would pick a cause "
                       "by fiat." % (top, winners)}
    return {"ok": True, "failure_class": winners[0], "matched": winners,
            "tier": top, "ambiguous": False,
            "why": "one class matched at tier %d%s" % (
                top, "" if len(hits) == 1 else
                " (lower tiers also matched and were treated as symptoms)")}


def assert_classified(template: dict[str, Any], *, where: str = "template",
                      require_cite: bool = False) -> str:
    """REFUSE a generator template that cannot say how it fails.

    Measured need: `FIELD_TDD_TEMPLATES["member_id"]` is the only one of eight
    with no `fail_class` and no `field_kind`, and `_tdd_template_for()`'s
    FALLBACK template has no `fail_class` either. So an unclassified template is
    reachable for any unknown field name, not just for one hand-written entry.

    `require_cite=True` additionally demands a `cite_ref`, so the class can be
    traced back to the definition it was chosen by. This is what would have
    caught my own error: a class filled because one was REQUIRED, with nothing to
    cite, cannot be checked afterwards.
    """
    cls = str((template or {}).get("fail_class") or "").strip()
    if not cls:
        raise FailureAxisError(
            "%s has NO fail_class, so a case generated from it cannot FAIL in any "
            "nameable way. Refusing at the generator (where it is USED) rather "
            "than at the display (where it would be papered over). template=%r"
            % (where, template)
        )
    if require_cite:
        cite = str((template or {}).get("cite_ref") or "").strip()
        if not cite:
            raise FailureAxisError(
                "%s declares fail_class=%r but carries NO cite_ref, so the class "
                "cannot be checked against the definition it was chosen by. "
                "Expected %s. A class with nothing to cite is a typed guess."
                % (where, cls, CLASS_CITE.get(cls, "a registered definition"))
            )
    return cls


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            print(json.dumps(ensure_axis(conn), indent=2, ensure_ascii=False))
        print(json.dumps({"registered_classes": registered_classes(conn)},
                         indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()