# -*- coding: utf-8 -*-
"""condition_based_waiting.py — wait for a CONDITION, not a fixed duration.

WHY THIS FILE EXISTS
--------------------
`docs/report_skill_set_for_finding_problems_zh.md` §3 (C2) names the gap:

    「Ctrl+Alt+K 之後 `time.sleep(1.4)` — 固定 sleep，正正係
      `condition-based-waiting` 要取代嘅嘢」

A fixed sleep is wrong in BOTH directions:
  - too short -> the next action runs before the UI is ready (flaky failure)
  - too long  -> every run pays the worst case (slow, and hides the real timing)

A condition wait returns the moment the condition holds, and fails LOUDLY on
timeout instead of proceeding into an unknown state. That second property is the
important one: a fixed sleep that expires silently lets the next action run
against a UI that never became ready, which is how a "flaky" failure becomes an
unexplainable one.

Pure: no I/O. The caller supplies the predicate.
"""
from __future__ import annotations

import time
from typing import Any, Callable

DEFAULT_TIMEOUT = 3.0
DEFAULT_POLL = 0.05


class ConditionTimeout(RuntimeError):
    """Raised when a condition never became true within the timeout.

    Loud by design: the caller must NOT proceed. Proceeding after a silent
    timeout is the failure this module exists to prevent.
    """

    def __init__(self, description: str, timeout: float, elapsed: float,
                 last: Any = None):
        self.description = description
        self.timeout = timeout
        self.elapsed = elapsed
        self.last = last
        super().__init__(
            "condition never became true within %.2fs: %s (last=%r)"
            % (timeout, description or "unnamed condition", last)
        )


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = DEFAULT_POLL,
    description: str = "",
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> float:
    """Poll `predicate` until it is truthy. Return the elapsed seconds.

    Returns as soon as the condition holds — it does NOT wait out the timeout.
    Raises ConditionTimeout when the condition never holds, so the caller cannot
    silently continue into an unknown state.

    `clock` and `sleeper` are injectable so the proof can run without real time.
    """
    start = clock()
    last: Any = None
    while True:
        try:
            last = predicate()
        except Exception as e:  # a raising predicate is "not yet true"
            last = "%s: %s" % (type(e).__name__, e)
        else:
            if last:
                return clock() - start
        elapsed = clock() - start
        if elapsed >= timeout:
            raise ConditionTimeout(description, timeout, elapsed, last)
        sleeper(min(poll, max(0.0, timeout - elapsed)))


def wait_until_or_false(
    predicate: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = DEFAULT_POLL,
    description: str = "",
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    """Like `wait_until` but returns False instead of raising.

    Use ONLY where a caller genuinely has a fallback path. Prefer `wait_until`:
    a boolean return is easy to ignore, and ignoring it is the silent-timeout
    failure this module exists to prevent.
    """
    try:
        wait_until(predicate, timeout=timeout, poll=poll,
                   description=description, clock=clock, sleeper=sleeper)
        return True
    except ConditionTimeout:
        return False


def fixed_sleep_is_wrong(seconds: float, *, observed_max: float | None = None) -> str:
    """Explain why a fixed sleep is the wrong tool, given an observed maximum.

    Returns a human-readable reason. Used by the audit to report which sleeps
    should become condition waits, and what timeout to use.
    """
    if observed_max is None:
        return ("fixed sleep %.2fs has no observed maximum — measure the real "
                "settle time, then use wait_until(..., timeout=2x that)" % seconds)
    if seconds < observed_max:
        return ("fixed sleep %.2fs is SHORTER than the observed max %.2fs — this "
                "is a latent flaky failure" % (seconds, observed_max))
    return ("fixed sleep %.2fs is longer than needed (observed max %.2fs) — it "
            "pays the worst case on every run" % (seconds, observed_max))


# ---------------------------------------------------------------------------
# runtime gates: a fixed sleep and a silent timeout must both be refused
# ---------------------------------------------------------------------------

class FixedSleepForbidden(RuntimeError):
    """Raised when a fixed sleep is about to be used before a UI action."""


class SilentTimeout(RuntimeError):
    """Raised when a timed-out wait is about to be treated as success."""


def assert_no_fixed_sleep(record: dict | None) -> dict:
    """Gate: refuse a fixed sleep before a UI action.

    A fixed sleep is wrong in both directions (too short -> flaky; too long ->
    pays the worst case), and its expiry is SILENT, which is how a flaky failure
    becomes an unexplainable one. Returns the record when no fixed sleep was
    used; raises FixedSleepForbidden otherwise.
    """
    if not isinstance(record, dict):
        raise FixedSleepForbidden(["record is not a dict"], )
    if record.get("fixed_sleep_used"):
        raise FixedSleepForbidden(
            ["a fixed sleep was used before a UI action — use "
             "wait_until(predicate, timeout=2x the observed max) instead"],
            )
    return record


def assert_timeout_handled(record: dict | None) -> dict:
    """Gate: refuse to treat a timed-out wait as success.

    `timed_out=True` means the condition NEVER held. Continuing from there runs
    the next action against an unknown state. Returns the record when the wait
    succeeded; raises SilentTimeout otherwise.
    """
    if not isinstance(record, dict):
        raise SilentTimeout(["record is not a dict"])
    if record.get("timed_out"):
        raise SilentTimeout(
            ["the condition never held within the timeout — the caller must NOT "
             "continue; a silent timeout is the failure this gate prevents"])
    return record


def may_proceed(record: dict | None) -> bool:
    """True when the wait record permits the next action. Never raises."""
    try:
        assert_no_fixed_sleep(record)
        assert_timeout_handled(record)
        return True
    except (FixedSleepForbidden, SilentTimeout):
        return False
