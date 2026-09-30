# -*- coding: utf-8 -*-
"""systematic_debug.py — the EXECUTABLE form of the systematic_debugging rules.

WHY THIS FILE EXISTS
--------------------
The contract `SKILL.SYSTEMATIC.DEBUGGING` declared rules (iron law, the 3+
stop rule, citation-or-discard) in a table. A rule in a table is not a gate:
`skill_tdd_runner.PROBES` had no probe for `SDB.*` cases, so every case would
return "no probe registered" and the declared protections were never executed
against anything. Same defect class as rule-without-gate.

So the rules live here as pure functions, and BOTH the proof script
(`_proof_stop_rule.py`) and the TDD runner probes import from here. One copy of
the rule, two consumers — otherwise the proof and the gate drift apart and the
proof starts certifying a rule the gate no longer runs.

Pure: no I/O, no DB, no network. Callers supply the state.
"""
from __future__ import annotations

STOP_THRESHOLD = 3

RETRY = "retry"
QUESTION_ARCHITECTURE = "question_architecture"


def decide_next_action(failed_fix_attempts: int,
                       *, threshold: int = STOP_THRESHOLD) -> str:
    """Next action after `failed_fix_attempts` failed fixes for ONE problem.

    Phase 4.5: once the threshold is reached the correct next action is to
    question the ARCHITECTURE. Attempting fix #threshold+1 is the exact
    behaviour this rule exists to prevent.
    """
    try:
        n = int(failed_fix_attempts)
    except (TypeError, ValueError):
        n = 0
    if n >= int(threshold):
        return QUESTION_ARCHITECTURE
    return RETRY


def may_propose_fix(root_cause_investigated: bool) -> bool:
    """The Iron Law as a predicate: no fixes before root cause investigation."""
    return bool(root_cause_investigated)


def keep_finding(evidence_ref: str) -> bool:
    """A finding with no evidence reference is DISCARDED, never downgraded.

    Mirrors superpowers' "no citation, no finding": the reference must be a
    non-empty `path:line` or a command.
    """
    return bool((evidence_ref or "").strip())


def replay(sequence, *, threshold: int = STOP_THRESHOLD):
    """Replay a recorded attempt sequence; return the attempt where the stop fires.

    `sequence` is an iterable of dicts with an "attempt" key (1-based).
    Returns the attempt number at which `decide_next_action` first returns
    QUESTION_ARCHITECTURE, or None if it never fires.
    """
    for step in sequence:
        n = step.get("attempt") if isinstance(step, dict) else None
        if n is None:
            continue
        if decide_next_action(n, threshold=threshold) == QUESTION_ARCHITECTURE:
            return n
    return None


# ---------------------------------------------------------------------------
# runtime gate: the stop rule must BLOCK, not merely advise
# ---------------------------------------------------------------------------

class ArchitectureReviewRequired(RuntimeError):
    """Raised instead of permitting fix attempt #threshold+1."""


def assert_may_attempt_fix(
    failed_fix_attempts: int,
    *,
    architecture_questioned: bool = False,
    threshold: int = STOP_THRESHOLD,
) -> str:
    """Gate at the point of action: refuse a further fix when the stop rule fires.

    A rule that is only written down is prose. `env_task_proof` learned this the
    hard way: `assert_rect_ok()` had to RAISE at the click site, because a
    recorded check nobody consults changes nothing. Same here — the 3+ rule only
    exists if attempting fix #4 is actually refused.

    Returns the action when the attempt is allowed; raises
    ArchitectureReviewRequired when it is not.
    """
    action = decide_next_action(failed_fix_attempts, threshold=threshold)
    if action == QUESTION_ARCHITECTURE and not architecture_questioned:
        raise ArchitectureReviewRequired(
            "%d fixes have failed (threshold %d). Attempting fix #%d is refused: "
            "question the architecture first, then re-set the counter with "
            "architecture_questioned=True."
            % (int(failed_fix_attempts), int(threshold), int(failed_fix_attempts) + 1)
        )
    return action
