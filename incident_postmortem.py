# -*- coding: utf-8 -*-
"""incident_postmortem.py — pre-declared triggers + a review gate.

WHY THIS FILE EXISTS
--------------------
`docs/report_skill_set_for_finding_problems_zh.md` §3 (C1) names the gap:

    「教訓係我想起才寫；冇任何觸發條件話『呢個算係事故』」

and §2:

    「事故觸發條件**事前**定義」

Source: Google SRE Book ch.15 (postmortem culture). The transferable parts are
the TRIGGER LIST and the REVIEW GATE; the rest is culture and cannot be
installed. The SRE rule this encodes:

    「未經覆核嘅事後報告等於從來冇存在過。」
    (An unreviewed postmortem is a postmortem that never happened.)

Two properties matter, and both are gates rather than prose:

  1. TRIGGERS ARE DECLARED BEFORE THE INCIDENT. A trigger invented after the
     fact can always be chosen to exclude the incident. So the trigger list is
     data, and `classify_incident()` decides from it — not from judgement.
  2. AN UNREVIEWED POSTMORTEM BLOCKS. `assert_reviewed()` refuses to let a
     draft postmortem count as closed.

Pure: no I/O, no DB. Callers supply the incident.
"""
from __future__ import annotations

# Triggers declared UP FRONT. Each is (key, description, predicate-name).
# The predicate names map to `_TRIGGER_TESTS` below, so a trigger cannot be
# declared without an executable test — the "rule in a table is not a gate"
# defect that `systematic_debug.py` documents.
TRIGGERS: tuple[tuple[str, str], ...] = (
    ("repeat_same_class",
     "the same class of error recurred in one session"),
    ("fix_attempts_ge_3",
     "3 or more fix attempts failed on one problem"),
    ("architecture_change_required",
     "the working fix required an architecture change, not a parameter change"),
    ("false_pass_recorded",
     "a PASS was recorded that later proved wrong"),
    ("evidence_destroyed",
     "a gate discarded or overwrote real provenance"),
    ("human_asked_after_3",
     "a human was asked for help after 3+ failed attempts"),
)

TRIGGER_KEYS = tuple(k for k, _d in TRIGGERS)

# Postmortem lifecycle. `reviewed` is the only state that counts as closed.
STATES = ("draft", "reviewed", "rejected")


def _trigger_tests() -> dict:
    """The executable test per trigger. Kept in one place so a declared trigger
    without a test is impossible."""
    return {
        "repeat_same_class": lambda inc: int(inc.get("same_class_count") or 0) >= 2,
        "fix_attempts_ge_3": lambda inc: int(inc.get("failed_fix_attempts") or 0) >= 3,
        "architecture_change_required": lambda inc: bool(
            inc.get("architecture_change_required")),
        "false_pass_recorded": lambda inc: bool(inc.get("false_pass_recorded")),
        "evidence_destroyed": lambda inc: bool(inc.get("evidence_destroyed")),
        "human_asked_after_3": lambda inc: (
            bool(inc.get("human_asked")) and
            int(inc.get("failed_fix_attempts") or 0) >= 3
        ),
    }


def fired_triggers(incident: dict | None) -> list[str]:
    """Which pre-declared triggers this incident fires. Empty = not an incident."""
    if not isinstance(incident, dict):
        return []
    tests = _trigger_tests()
    return [k for k in TRIGGER_KEYS if tests[k](incident)]


def is_incident(incident: dict | None) -> bool:
    """True when at least one pre-declared trigger fires."""
    return bool(fired_triggers(incident))


def classify_incident(incident: dict | None) -> dict:
    """Return {is_incident, triggers, requires_postmortem}.

    `requires_postmortem` is True whenever any trigger fires — there is no
    "minor incident" escape hatch, because that is exactly the judgement call
    the pre-declared list exists to remove.
    """
    fired = fired_triggers(incident)
    return {
        "is_incident": bool(fired),
        "triggers": fired,
        "requires_postmortem": bool(fired),
    }


# ---------------------------------------------------------------------------
# runtime gate: an unreviewed postmortem does not count
# ---------------------------------------------------------------------------

class PostmortemRequired(RuntimeError):
    """Raised when an incident is being closed without a reviewed postmortem."""

    def __init__(self, reasons: list[str], incident: dict | None = None):
        self.reasons = list(reasons)
        self.incident = incident
        super().__init__(
            "postmortem required — refusing to close: %s"
            % ("; ".join(self.reasons) or "no reason given")
        )


def assert_reviewed(postmortem: dict | None, incident: dict | None = None) -> dict:
    """Gate: refuse to close an incident without a REVIEWED postmortem.

    Encodes the SRE rule "an unreviewed postmortem is a postmortem that never
    happened". A `draft` postmortem is not closed; it is pending.

    Also requires the postmortem to name its triggers and carry at least one
    action item with an owner — a postmortem with no owner is a wish.
    """
    reasons: list[str] = []

    if is_incident(incident) and not isinstance(postmortem, dict):
        raise PostmortemRequired(
            ["an incident fired %r but no postmortem exists"
             % (fired_triggers(incident),)], incident)

    if not isinstance(postmortem, dict):
        raise PostmortemRequired(["postmortem is not a dict"], incident)

    state = str(postmortem.get("state") or "").strip().lower()
    if state not in STATES:
        reasons.append("state %r is not one of %s" % (state or "?", list(STATES)))
    elif state != "reviewed":
        reasons.append(
            "state is %r — an unreviewed postmortem is a postmortem that never "
            "happened" % state)

    if not postmortem.get("triggers"):
        reasons.append("no triggers recorded — the postmortem must name what fired")

    actions = postmortem.get("action_items") or []
    if not actions:
        reasons.append("no action items — a postmortem with no action is a wish")
    else:
        ownerless = [a for a in actions
                     if not str((a or {}).get("owner") or "").strip()]
        if ownerless:
            reasons.append("%d action item(s) have no owner" % len(ownerless))

    if reasons:
        raise PostmortemRequired(reasons, incident)
    return postmortem


def may_close(incident: dict | None, postmortem: dict | None) -> bool:
    """True when the incident may be closed. Never raises."""
    try:
        assert_reviewed(postmortem, incident)
        return True
    except PostmortemRequired:
        return False
