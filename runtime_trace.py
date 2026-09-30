"""runtime_trace.py -- THE CONNECTOR: a runtime fault becomes a ROW, and a row
reaches a FACTOR report in the system's ONE language.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "watchdog and heartbeat is ready now? and they are the source for runtime
     which can help coding verify to debug or improve quality. how to has this in
     full cycle? all can be tracable ... trace table is need by DB driven now?
     and report the factor by logic generator to have unique language at the
     system to help that can work easy?"

MEASURED, and the answer is CONNECTORS, NOT NEW SYSTEMS:

  1. The liveness UNIT works (heartbeat ALIVE 250s < 900s; watchdog DEAD 2560s >
     300s) and is cited (`watchdog_health.py:74`).
  2. The alarm STOPS at a toast: `watchdog_health.alarm_once` calls
     `openclaw_bridge.try_notify` and writes NOTHING. Measured: no `fault_event`
     write anywhere in that module.
  3. `fault_factor_trace` EXISTS, has 0 rows, and has NO writer anywhere in the
     repo. `route_registry.py:117` already named the defect:
         "`capability_binding` and `fault_factor_trace` were built and never
          written, and a declared-but-empty register is the defect this work
          answers."
  4. The UNIQUE LANGUAGE already exists: `logic_generator.dimension_wording(conn,
     subject_kind)` returns the per-kind 5W1H wording DERIVED from
     `skill_5w1h.DIMENSIONS` + `dimension_binding_registry`.

So this module adds NO new store, NO new language and NO new unit. It makes the
existing ones REACHABLE from each other:

    measure -> record_fault (fault_event + fault_event_fact)   [the persisted row]
            -> record_trace (fault_factor_trace)                [the fault->factor link]
            -> report (logic_generator.dimension_wording)       [the ONE language]

WHAT THIS MODULE MUST NEVER DO
------------------------------
Kill, terminate or restart anything. MEASURED reason (`helper_watchdog.py:651`):
killing from `adopt` produced FALSE downs. A WEDGED component is therefore
RECORDED and ESCALATED, never killed — and `no_kill_path()` proves it over the
AST, not over the text (a text scan would match this docstring, which NAMES the
forbidden verbs in order to forbid them).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The term this module is named for. It is registered in `terminology_registry`
# BEFORE use (`ensure_term`), because a name that is not a registered term makes
# every later reader pick the wrong referent (terminology-register rule).
TERM_KEY = "runtime_trace"
TERM_DEFINITION = (
    "A DB-driven record of a RUNTIME fault and the factor it violates: the "
    "`fault_event` row (what happened, with its facts) plus the "
    "`fault_factor_trace` row (which factor, how often, against how many) whose "
    "`cite_ref` is the measurement that produced it. It is NOT a log file and NOT "
    "a notification: a runtime_trace is only complete when a reader can OPEN a "
    "row and check the reference."
)
TERM_CITE = "runtime_trace.py:1"

# THE FACTOR this connector reports against. Its unit is the SAME unit the
# liveness proof uses, so the number is auditable rather than a sentiment
# (`factor_first_principle`: a number without a unit cannot be audited).
LIVENESS_FACTOR_KEY = "runtime_liveness_evidence"
LIVENESS_FACTOR_NAME = "Runtime Liveness Evidence"
LIVENESS_FACTOR_RULE = (
    "Every supervised runtime component must be SEEN writing its own evidence "
    "within its declared staleness window; a unit that is not ALIVE must leave a "
    "persisted trace, because a component that dies silently is exactly what a "
    "liveness check exists to catch."
)
LIVENESS_FACTOR_ACTION = (
    "Measure seconds since the component's newest evidence; record a fault_event "
    "+ fault_factor_trace when it exceeds the window; NEVER kill or restart it."
)
# `metric_kind` IS CONSTRAINED BY THE REGISTRY, and the plan's first choice was
# refused. MEASURED 2026-09-24: `skill_factor.register_factor` accepts ONLY
# `boolean, count, pct, score_0_100` and RAISES `FactorError` for `seconds`
# (`skill_factor.py:1403`: "A metric that cannot be scored is decorative.").
#
# So the number is carried by `count` — seconds IS a count of seconds — with the
# staleness UNIT and the threshold in `metric_target`. The plan's §7 recommendation
# (a NUMBER, not a boolean) is preserved: `count` holds the number, so the reading
# is still auditable, and the boolean ALIVE/DEAD state stays DERIVABLE from it.
LIVENESS_METRIC_KIND = "count"
# THE UNIT OPENS WITH ITS OWN KIND, and THE TARGET IS A BARE VALUE.
#
# MEASURED 2026-09-26: this row was the ONLY one of 40 that broke BOTH conventions.
# `metric_target` was `'<= 300'` (the other 39 carry a bare value: '0' x26, '100'
# x11, 'true' x2, '0.97' x1) and `metric_unit` was `'seconds since ...'` (the other
# 39 read `'<kind> of <subject>'`).
#
# The convention is not cosmetic: `metric_kind_derive.py` derives the vocabulary
# from the EVIDENCE that "`metric_kind` IS the PREFIX of `metric_unit`", and MEASURED
# that the two sources agree for 38/40 — the 2 that disagree being EXACTLY the 2 the
# auditor calls not-measurable. Relaxing the auditor to admit `'<= 300'` would have
# broken the derivation that AGREES with it. So the SPELLING was fixed, not the rule.
#
# The threshold VALUE is unchanged: 300. "exceeds the window" is the comparison, and
# it lives in `LIVENESS_FACTOR_RULE` below, where it is prose a reader can check.
LIVENESS_METRIC_UNIT = "count of seconds since the component wrote its newest evidence"
LIVENESS_METRIC_TARGET = "300"
LIVENESS_CITE = "watchdog_health.py:74"


class RuntimeTraceError(Exception):
    """Refused -- nothing written. A refusal is louder than a wrong row."""


# ---------------------------------------------------------------------------
# terminology FIRST (a name needs a registered term before it is used)
# ---------------------------------------------------------------------------


def ensure_term(conn: sqlite3.Connection, *, commit: bool = True
                ) -> dict[str, Any]:
    """Register the `runtime_trace` term, idempotently. Returns the row.

    Idempotent because `add_term` refuses a duplicate key: a second call REPORTS
    the existing row instead of raising, so a caller can always ensure the term.
    """
    import terminology_registry as tr
    tr.ensure_schema(conn)
    existing = tr.get_term(conn, TERM_KEY)
    if existing:
        return {"ok": True, "created": False, "term_id": int(existing["term_id"]),
                "term_key": TERM_KEY}
    r = tr.add_term(conn, TERM_KEY, definition=TERM_DEFINITION, cite_ref=TERM_CITE,
                    term_kind="part", commit=commit)
    if not r.get("ok"):
        raise RuntimeTraceError("the term %r could not be registered: %s"
                                % (TERM_KEY, r))
    return {"ok": True, "created": True, "term_id": int(r["term_id"]),
            "term_key": TERM_KEY}


# ---------------------------------------------------------------------------
# the fault store (REUSED shape; this module only writes the row)
# ---------------------------------------------------------------------------


def _upsert_fact(conn: sqlite3.Connection, event_id: int, keyword: str,
                 value: Any, value_type: str = "string") -> None:
    """Set ONE fact on ONE event, REPLACING an earlier value for that keyword.

    WHY UPDATE RATHER THAN APPEND: the dedup exists so a repeated sighting does
    not grow the table, and appending a fact per tick would grow `fault_event_fact`
    instead — the same unbounded growth one level down. So a keyword that already
    exists on this event is UPDATED, and only a NEW keyword inserts.
    """
    if value is None:
        return
    text = str(value)
    row = conn.execute(
        "SELECT id FROM fault_event_fact WHERE event_id=? AND keyword=? "
        "ORDER BY id LIMIT 1", (event_id, keyword)).fetchone()
    if row:
        conn.execute("UPDATE fault_event_fact SET value_text=?, value_type=? "
                     "WHERE id=?", (text, value_type, int(row[0])))
    else:
        conn.execute(
            "INSERT INTO fault_event_fact (event_id, keyword, value_text, "
            "value_type, source) VALUES (?,?,?,?, 'watchdog')",
            (event_id, keyword, text, value_type))


def open_fault(conn: sqlite3.Connection, group_id: str) -> dict[str, Any] | None:
    """The OPEN `fault_event` row for a `group_id`, or None.

    THE DEDUP KEY IS THE TABLE'S OWN STATE, not a second rule: `group_id` is
    indexed (`idx_fault_event_group`) and `status='open'` is the declared
    vocabulary (`CHECK (status IN ('open','resolved'))`). One ongoing fault = one
    open row.
    """
    row = conn.execute(
        "SELECT * FROM fault_event WHERE group_id=? AND status='open' "
        "ORDER BY event_id DESC LIMIT 1", (str(group_id).strip(),)).fetchone()
    return dict(row) if row else None


def resolve_fault(conn: sqlite3.Connection, group_id: str, *,
                  cite_ref: str, reason: str = "",
                  event_id: int | None = None,
                  commit: bool = True) -> dict[str, Any]:
    """CLOSE an open fault: `status='resolved'` + `resolved_at`.

    `event_id` SELECTS WHICH OPEN ROW, and it is REQUIRED whenever a group has more
    than one open row. MEASURED, and this bit me: a producer that lacks the dedup
    can leave MANY open rows for ONE `group_id` (`pair_qc` had **12**), and a
    group-keyed close resolved only the NEWEST, leaving 11 open — a sweep would
    have reported success while the pile-up survived. So the caller that means to
    clear a condition passes each row's id explicitly.

    `reason` IS RECORDED WHEN GIVEN, because a close with only a citation says WHO
    claims it closed but not WHY. MEASURED, and this is why the parameter exists: a
    STALE SWEEP closes a fault whose producer went silent, and "the producer has
    been silent for 10 days" is the fact a later reader needs. The two closes stay
    DISTINGUISHABLE in the data (`resolved_reason` is present only when supplied),
    so an ack-close and a sweep-close cannot be confused.

    REFUSES when there is no open row: closing a fault that is not open would
    fabricate a lifecycle event.
    """
    if not str(cite_ref or "").strip():
        raise RuntimeTraceError("no citation, no resolution: cite_ref is required")
    if event_id is not None:
        row = conn.execute(
            "SELECT * FROM fault_event WHERE event_id=? AND status='open' AND "
            "group_id=?", (int(event_id), str(group_id).strip())).fetchone()
        row = dict(row) if row else None
        if not row:
            raise RuntimeTraceError(
                "event_id=%s is not an OPEN row of group_id=%r"
                % (event_id, group_id))
    else:
        row = open_fault(conn, group_id)
        if not row:
            raise RuntimeTraceError(
                "no OPEN fault for group_id=%r, so there is nothing to resolve"
                % group_id)
    conn.execute(
        "UPDATE fault_event SET status='resolved', resolved_at=datetime('now') "
        "WHERE event_id=?", (int(row["event_id"]),))
    _upsert_fact(conn, int(row["event_id"]), "resolved_by_cite", cite_ref)
    if str(reason or "").strip():
        _upsert_fact(conn, int(row["event_id"]), "resolved_reason", str(reason).strip())
    if commit:
        conn.commit()
    return {"ok": True, "event_id": int(row["event_id"]),
            "group_id": row["group_id"], "status": "resolved",
            "reason": str(reason or "").strip() or None}


def _window_for_group(group_id: str) -> int | None:
    """The liveness window for a `runtime_<subject>` group, or None.

    THE WINDOW IS NOT INVENTED HERE. It is the SAME `stale_after_sec` the
    liveness unit uses, so a tick and its threshold cannot drift — the rule
    `install_watchdog_keepalive` already follows. A second threshold would be a
    second copy of one fact.
    """
    gid = str(group_id or "").strip()
    subject = gid[len("runtime_"):] if gid.startswith("runtime_") else gid
    try:
        import watchdog_health as wh
        spec = wh.LIVENESS_UNITS.get(subject)
        if spec:
            return int(spec.get("stale_after_sec") or 0) or None
    except Exception:
        return None
    return None


def should_report(conn: sqlite3.Connection, group_id: str, *,
                  window_sec: int | None = None,
                  now: str | None = None) -> dict[str, Any]:
    """Is a chat report DUE for this fault? Derived from the OPEN row.

    THE RULE (option (b), taken at approval): report when there is NO open row
    yet (a NEW occurrence), OR the last report for the open row is older than the
    window. The window defaults to the SUBJECT'S liveness `stale_after_sec`, so a
    still-open fault REMINDS once per window instead of flooding once per tick.

    "ALREADY REPORTED" IS STORED ON THE OPEN ROW (`reported_at` fact), so the
    state lives with the fault and not in a process's memory — a restart cannot
    re-flood.
    """
    gid = str(group_id or "").strip()
    row = open_fault(conn, gid)
    if not row:
        return {"due": True, "why": "no open fault yet: this is a NEW occurrence",
                "open_event_id": None, "last_report_at": None,
                "window_sec": window_sec or _window_for_group(gid)}
    eid = int(row["event_id"])
    last = conn.execute(
        "SELECT value_text FROM fault_event_fact WHERE event_id=? AND "
        "keyword='reported_at' ORDER BY id LIMIT 1", (eid,)).fetchone()
    last_at = str(last[0]) if last and str(last[0]).strip() not in ("", "None") else None
    win = window_sec or _window_for_group(gid)
    if not last_at:
        return {"due": True, "why": "open fault has never been reported",
                "open_event_id": eid, "last_report_at": None, "window_sec": win}
    if not win:
        return {"due": False, "why": "already reported and no window to remind by",
                "open_event_id": eid, "last_report_at": last_at, "window_sec": None}
    # The comparison is done by SQLite so the clock is ONE clock (the DB's), the
    # same rule `watchdog_health.freshness` follows.
    ref = str(now or "now")
    try:
        age = conn.execute(
            "SELECT (julianday(?) - julianday(?)) * 86400.0",
            (ref if ref != "now" else _now_text(conn), last_at)).fetchone()[0]
    except sqlite3.OperationalError:
        age = None
    if age is None:
        return {"due": True, "why": "the age could not be computed",
                "open_event_id": eid, "last_report_at": last_at, "window_sec": win}
    if float(age) < 0:
        # A NEGATIVE age means the clocks disagree. Report rather than silently
        # suppress, so a clock fault cannot silence the alarm.
        return {"due": True, "why": "negative age (clock mismatch): reporting",
                "open_event_id": eid, "last_report_at": last_at, "window_sec": win}
    return {"due": float(age) >= float(win),
            "why": "%ss since the last report (window %ss)"
                   % (round(float(age)), win),
            "open_event_id": eid, "last_report_at": last_at, "window_sec": win,
            "age_seconds": round(float(age), 1)}


def _now_text(conn: sqlite3.Connection) -> str:
    """The DB's own `now`, so every comparison uses ONE clock."""
    return str(conn.execute("SELECT datetime('now')").fetchone()[0])


def mark_reported(conn: sqlite3.Connection, group_id: str, *, message_id: int,
                  report_id: int | None = None, commit: bool = True
                  ) -> dict[str, Any]:
    """Record that the OPEN fault was reported (the cooldown's state).

    It stores the message id AND the report id, so a reader can open the message
    (`chat_center_message.id`) and the trace (`fault_factor_trace.trace_id`) from
    the fault row itself.
    """
    row = open_fault(conn, group_id)
    if not row:
        raise RuntimeTraceError("no OPEN fault for group_id=%r to mark reported"
                                % group_id)
    eid = int(row["event_id"])
    _upsert_fact(conn, eid, "reported_at", _now_text(conn))
    _upsert_fact(conn, eid, "report_message_id", int(message_id), "number")
    if report_id is not None:
        _upsert_fact(conn, eid, "report_trace_id", int(report_id), "number")
    if commit:
        conn.commit()
    return {"ok": True, "event_id": eid, "message_id": int(message_id)}


def fault_report_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many fault rows EXIST vs how many faults are CURRENTLY open.

    A REPORTED pair of numbers, because the dedup's whole point is that the two
    diverge over a fault's life: `rows_total` grows per OCCURRENCE while
    `rows_open` stays at the number of ongoing conditions.
    """
    return {
        "rows_total": int(conn.execute("SELECT COUNT(*) FROM fault_event"
                                       ).fetchone()[0]),
        "rows_open": int(conn.execute("SELECT COUNT(*) FROM fault_event WHERE "
                                      "status='open'").fetchone()[0]),
        "rows_resolved": int(conn.execute("SELECT COUNT(*) FROM fault_event "
                                          "WHERE status='resolved'").fetchone()[0]),
        "facts_total": int(conn.execute("SELECT COUNT(*) FROM fault_event_fact"
                                        ).fetchone()[0]),
    }


def record_fault(conn: sqlite3.Connection, *, subject: str, state: str,
                 reason: str, cite_ref: str, worker_id: int | None = None,
                 evidence_error: str | None = None,
                 facts: dict[str, Any] | None = None,
                 dedup: bool = True,
                 commit: bool = True) -> dict[str, Any]:
    """Record a fault, DEDUPING against an OPEN row for the same subject.

    THE DEFECT THIS FIXES (MEASURED 2026-09-24): one ongoing wedged watchdog
    produced **7** `fault_event` rows — one per 5-minute keep-alive tick — and 63
    facts, because every tick inserted a new row for the SAME condition. Every
    reader that counts faults over-counted by the number of ticks the fault
    stayed alive.

    THE DEDUP KEY IS THE TABLE'S OWN STATE: `group_id` + `status='open'`. One
    ongoing fault is one open row. A SECOND sighting UPDATES that row's evidence
    and advances `latest_sighting_at` / `sighting_count`, so the START
    (`detect_at`) and the LATEST evidence BOTH survive.

    A fault that was RESOLVED and then returns INSERTS A NEW ROW — a recurrence is
    new information and is never merged into the old occurrence.

    `source='watchdog'` on the facts, the column's own default.
    """
    for name, val in (("subject", subject), ("state", state),
                      ("reason", reason), ("cite_ref", cite_ref)):
        if not str(val or "").strip():
            raise RuntimeTraceError("%s is required (no %s, no fault)" % (name, name))
    group_id = "runtime_%s" % str(subject).strip()
    fault_type = "runtime_%s_%s" % (str(subject).strip(), str(state).strip().lower())
    payload: dict[str, Any] = {"subject": subject, "state": state,
                               "reason": reason, "cite_ref": cite_ref}
    if facts:
        payload.update(facts)

    existing = open_fault(conn, group_id) if dedup else None
    if existing:
        event_id = int(existing["event_id"])
        # The evidence is UPDATED; `detect_at` is NOT moved, because the fault
        # STARTED earlier and moving it would erase the duration.
        conn.execute(
            "UPDATE fault_event SET fault_type=?, evidence_error=? WHERE event_id=?",
            (fault_type, evidence_error or reason, event_id))
        seen = conn.execute(
            "SELECT value_text FROM fault_event_fact WHERE event_id=? AND "
            "keyword='sighting_count'", (event_id,)).fetchone()
        n = int(seen[0]) + 1 if seen and str(seen[0]).isdigit() else 1
        _upsert_fact(conn, event_id, "latest_sighting_at", _now_text(conn))
        _upsert_fact(conn, event_id, "sighting_count", n, "number")
        written: list[str] = []
        for k, v in payload.items():
            if v is None:
                continue
            _upsert_fact(conn, event_id, k, v,
                         "number" if isinstance(v, (int, float))
                         and not isinstance(v, bool) else "string")
            written.append(k)
        if commit:
            conn.commit()
        return {"ok": True, "event_id": event_id, "group_id": group_id,
                "fault_type": fault_type, "facts_written": written,
                "deduped": True, "sighting_count": n}

    cur = conn.execute(
        "INSERT INTO fault_event (worker_id, group_id, fault_type, "
        "evidence_error, status) VALUES (?,?,?,?, 'open')",
        (worker_id, group_id, fault_type, evidence_error or reason))
    event_id = int(cur.lastrowid)
    written = []
    for k, v in payload.items():
        if v is None:
            continue
        text = str(v)
        vtype = "number" if isinstance(v, (int, float)) and not isinstance(v, bool) \
            else "string"
        conn.execute(
            "INSERT INTO fault_event_fact (event_id, keyword, value_text, "
            "value_type, source) VALUES (?,?,?,?, 'watchdog')",
            (event_id, k, text, vtype))
        written.append(k)
    _upsert_fact(conn, event_id, "sighting_count", 1, "number")
    if commit:
        conn.commit()
    return {"ok": True, "event_id": event_id, "group_id": group_id,
            "fault_type": fault_type, "facts_written": written,
            "deduped": False, "sighting_count": 1}


# ---------------------------------------------------------------------------
# the missing link: the fault -> factor trace row
# ---------------------------------------------------------------------------


def trace_key_for(ref_tag: str, factor_key: str) -> str:
    """The stable key for one (what failed, which factor) pair. Idempotent."""
    return "%s|%s" % (str(ref_tag).strip(), str(factor_key).strip())


def record_trace(conn: sqlite3.Connection, *, ref_tag: str, factor_key: str,
                 failure_count: int, total_count: int, reason: str,
                 cite_ref: str, traced_by: str = "runtime_trace.py",
                 commit: bool = True) -> dict[str, Any]:
    """Write ONE `fault_factor_trace` row. Idempotent on `trace_key`.

    THE COUNTS ARE MEASURED, NOT TYPED: `failure_count` / `total_count` must be
    supplied by a caller that MEASURED them, so the row's rate is a reading. A
    hand-typed count is the defect `factor_first_principle` forbids.

    REFUSES `total_count < failure_count` (a rate above 100% is not a
    measurement), and refuses an empty `cite_ref` — no citation, no finding.
    """
    if not str(ref_tag or "").strip():
        raise RuntimeTraceError("ref_tag is required (what failed?)")
    if not str(factor_key or "").strip():
        raise RuntimeTraceError("factor_key is required (which factor?)")
    if not str(cite_ref or "").strip():
        raise RuntimeTraceError("no citation, no trace: cite_ref is required")
    failure_count = int(failure_count)
    total_count = int(total_count)
    if failure_count < 0 or total_count < 0:
        raise RuntimeTraceError("counts cannot be negative")
    if total_count < failure_count:
        raise RuntimeTraceError(
            "total_count (%d) < failure_count (%d): a rate above 100%% is not a "
            "measurement" % (total_count, failure_count))
    trace_key = trace_key_for(ref_tag, factor_key)
    existing = conn.execute("SELECT trace_id FROM fault_factor_trace "
                            "WHERE trace_key=?", (trace_key,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE fault_factor_trace SET failure_count=?, total_count=?, "
            "reason=?, cite_ref=?, traced_by=? WHERE trace_key=?",
            (failure_count, total_count, reason, cite_ref, traced_by, trace_key))
        tid = int(existing[0])
        created = False
    else:
        cur = conn.execute(
            "INSERT INTO fault_factor_trace (trace_key, ref_tag, factor_key, "
            "failure_count, total_count, reason, cite_ref, traced_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trace_key, ref_tag, factor_key, failure_count, total_count, reason,
             cite_ref, traced_by))
        tid = int(cur.lastrowid)
        created = True
    if commit:
        conn.commit()
    return {"ok": True, "trace_id": tid, "trace_key": trace_key,
            "created": created, "failure_count": failure_count,
            "total_count": total_count}


# ---------------------------------------------------------------------------
# the ONE language: CONSUME logic_generator.dimension_wording, never a second one
# ---------------------------------------------------------------------------

# The subject kind whose 5W1H wording describes a RUNTIME component. It is a
# FUNCTION (the watchdog is a function/process that watches), which is why the
# subject kind is `function` rather than a new kind.
REPORT_SUBJECT_KIND = "function"


def report(conn: sqlite3.Connection, *, ref_tag: str,
           subject_kind: str = REPORT_SUBJECT_KIND) -> dict[str, Any]:
    """The trace + factor view, worded in the system's ONE language.

    THE LANGUAGE IS NOT DEFINED HERE. It is read from
    `logic_generator.dimension_wording(conn, subject_kind)`, so this report
    follows the registers and cannot drift from them. A second wording table
    would be the "one fact, two sources" defect this repo keeps removing.
    """
    import logic_generator as lg
    wording = lg.dimension_wording(conn, subject_kind)
    traces = [dict(r) for r in conn.execute(
        "SELECT trace_id, trace_key, ref_tag, factor_key, failure_count, "
        "total_count, reason, cite_ref, traced_by, created_at "
        "FROM fault_factor_trace WHERE ref_tag=? ORDER BY trace_id", (ref_tag,))]
    factors: list[dict[str, Any]] = []
    for t in traces:
        # MEASURED: `skill_factor_registry` has NO `cite_ref` column —
        # `register_factor` only VALIDATES a cite (`cd.assert_cited`, line 1404)
        # and stores the factor row. So the factor's citation is the MODULE
        # CONSTANT `LIVENESS_CITE`, and it is reported as `cite_ref` here rather
        # than SELECTed from a column that does not exist.
        row = conn.execute(
            "SELECT factor_key, name, metric_kind, metric_unit, metric_target, "
            "rule_definition FROM skill_factor_registry "
            "WHERE factor_key=?", (t["factor_key"],)).fetchone()
        if row:
            d = dict(row)
            d["cite_ref"] = (LIVENESS_CITE if d["factor_key"] == LIVENESS_FACTOR_KEY
                             else "NA")
            factors.append(d)
    rate = None
    if traces:
        fc = sum(int(t["failure_count"]) for t in traces)
        tc = sum(int(t["total_count"]) for t in traces)
        rate = (float(fc) / tc) if tc else None
    return {"ok": True, "ref_tag": ref_tag, "subject_kind": subject_kind,
            "wording": wording, "traces": traces, "factors": factors,
            "failure_count": sum(int(t["failure_count"]) for t in traces),
            "total_count": sum(int(t["total_count"]) for t in traces),
            "failure_rate": rate,
            "language_source": "logic_generator.dimension_wording"}


# ---------------------------------------------------------------------------
# the ONE factor (registered, measurable, with the liveness unit)
# ---------------------------------------------------------------------------


def ensure_liveness_factor(conn: sqlite3.Connection, *, commit: bool = True
                           ) -> dict[str, Any]:
    """Register the ONE liveness factor, with the unit the liveness proof uses.

    Idempotent: an existing row is REPORTED, not duplicated.
    """
    import skill_factor as sf
    sf.ensure_schema(conn)
    existing = conn.execute(
        "SELECT factor_id FROM skill_factor_registry WHERE factor_key=?",
        (LIVENESS_FACTOR_KEY,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "factor_id": int(existing[0]),
                "factor_key": LIVENESS_FACTOR_KEY}
    r = sf.register_factor(
        conn, factor_key=LIVENESS_FACTOR_KEY, name=LIVENESS_FACTOR_NAME,
        rule_definition=LIVENESS_FACTOR_RULE, action=LIVENESS_FACTOR_ACTION,
        metric_kind=LIVENESS_METRIC_KIND, metric_unit=LIVENESS_METRIC_UNIT,
        metric_target=LIVENESS_METRIC_TARGET, proof_prefix="proof_runtime",
        skill_key=None, applies_to="*", sort_order=100, cite_ref=LIVENESS_CITE)
    if not r.get("ok", True):
        raise RuntimeTraceError("the liveness factor could not be registered: %s" % r)
    fid = r.get("factor_id")
    if fid is None:
        fid = conn.execute("SELECT factor_id FROM skill_factor_registry "
                           "WHERE factor_key=?", (LIVENESS_FACTOR_KEY,)).fetchone()[0]
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "factor_id": int(fid),
            "factor_key": LIVENESS_FACTOR_KEY}


# ---------------------------------------------------------------------------
# the table-registry row (a real table the register did not know about)
# ---------------------------------------------------------------------------


def ensure_table_registered(conn: sqlite3.Connection, table_key: str,
                            *, description: str = "NA", commit: bool = True
                            ) -> dict[str, Any]:
    """Register `table_key` in `db_table_registry` (idempotent).

    MEASURED: `fault_factor_trace` is a real table with NO `db_table_registry`
    row, while `fault_event` (T-25) and `fault_event_fact` (T-26) are present.
    A table the register does not know about cannot be reached through the
    register — the same "declared-but-empty" family this plan answers.
    """
    row = conn.execute("SELECT db_table_id FROM db_table_registry WHERE table_key=?",
                       (table_key,)).fetchone()
    if row:
        return {"ok": True, "created": False, "db_table_id": int(row[0]),
                "table_key": table_key}
    cur = conn.execute(
        "INSERT INTO db_table_registry (table_key, name, description, is_active, "
        "version) VALUES (?,?,?,1,'1')", (table_key, table_key, description))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "db_table_id": int(cur.lastrowid),
            "table_key": table_key}


# ---------------------------------------------------------------------------
# the alarm -> row wiring (WRITE FIRST, notify LAST)
# ---------------------------------------------------------------------------


def record_alarm(conn: sqlite3.Connection, alarm: dict[str, Any], *,
                 worker_id: int | None = None, report_chat: bool = True,
                 worker_key: str = "", commit: bool = True
                 ) -> dict[str, Any]:
    """Persist ONE problem from a `watchdog_health.alarm_once` result.

    `alarm` is that function's own return value, so the trace is derived from the
    MEASUREMENT and not from a re-reading. Nothing is written when there is no
    problem: a healthy runtime leaves no fault row (otherwise the table would
    fill with "fine" and a real fault would not stand out).
    """
    problems = list(alarm.get("problems") or [])
    if not problems:
        return {"ok": True, "written": [], "reason": "no problem to record"}
    written: list[dict[str, Any]] = []
    for p in problems:
        name = str(p.get("name") or "unknown")
        state = str(p.get("state") or "UNKNOWN")
        why = str(p.get("why") or p.get("unit") or "")
        cref = str(p.get("cite_ref") or LIVENESS_CITE)
        facts = {"unit": p.get("unit"), "seconds": p.get("seconds"),
                 "count": p.get("count"),
                 "stale_after_sec": p.get("stale_after_sec")}
        # THE COOLDOWN IS CHECKED BEFORE THE REPORT, not before the fault: the
        # FAULT row is updated every tick (that is the dedup), while the chat
        # MESSAGE is throttled. `should_report` reads the OPEN row's `reported_at`
        # fact, so the state lives with the fault.
        due = should_report(conn, "runtime_%s" % name)
        ev = record_fault(conn, subject=name, state=state, reason=why,
                          cite_ref=cref, worker_id=worker_id,
                          evidence_error=alarm_reason_text(p), facts=facts,
                          commit=False)
        # The factor row: MEASURED counts. `total_count` is the number of
        # supervised subjects the alarm examined; `failure_count` is how many
        # were not ALIVE. Both come from the alarm itself.
        total = int(alarm.get("checked") or len(alarm.get("states") or {}) or 1)
        tr = record_trace(conn, ref_tag=name, factor_key=LIVENESS_FACTOR_KEY,
                          failure_count=1, total_count=max(total, 1),
                          reason=why, cite_ref=cref,
                          traced_by="runtime_trace.record_alarm", commit=False)
        written.append({"subject": name, "state": state,
                        "event_id": ev["event_id"], "trace_id": tr["trace_id"],
                        "deduped": bool(ev.get("deduped")),
                        "sighting_count": ev.get("sighting_count"),
                        "report_due": bool(due.get("due")),
                        "report_why": due.get("why")})
    if commit:
        conn.commit()
    # ---- THE ESCALATION: a fault becomes a CHAT MESSAGE (plan WATCHDOG.CHAT.REPORT)
    # MEASURED before this: the fault was a ROW and the escalation was a best-effort
    # toast. A toast has no row, so it cannot be read back, cited or counted — the
    # user's words: "report to module :chat to have the help". The report is
    # written LAST, after the rows are committed, so a report failure cannot lose
    # the measurement (the same write-first rule the alarm follows).
    reports: list[dict[str, Any]] = []
    if report_chat and written:
        reports = _emit_chat_reports(conn, written, worker_key=worker_key)
    return {"ok": True, "written": written,
            "event_ids": [w["event_id"] for w in written],
            "trace_ids": [w["trace_id"] for w in written],
            "chat_reports": reports,
            "deduped": [w["subject"] for w in written if w.get("deduped")]}


def _emit_chat_reports(conn: sqlite3.Connection, written: list[dict[str, Any]],
                       *, worker_key: str = "") -> list[dict[str, Any]]:
    """Write ONE chat report per fault, THROTTLED by the open row's cooldown.

    THE COOLDOWN IS THE POINT: without it, every tick of a still-open fault would
    write another message (the defect this plan fixes). `should_report` decides
    from the OPEN row's `reported_at` fact, so a fault keeps ONE message per
    window instead of one per tick, and a resolve-then-return gets a NEW message.

    BEST-EFFORT ON PURPOSE: the rows are already committed, so a report failure
    must not undo the measurement — but it is RETURNED, so a broken report is
    visible rather than silent.

    A NEW REMINDER RETIRES THE PREVIOUS ONE (decision (a) at approval). MEASURED:
    one ongoing fault collected 2 `ask` messages (84 at 07:14:36 and 85 at
    07:24:03) because the window-reminder is deliberately periodic, so the amber
    list grew by one per window. The previous reminder is superseded by calling the
    EXISTING `report_ack.supersede_siblings`, so the list holds exactly ONE
    actionable item per occurrence while the history is kept.
    """
    try:
        import chat_report as cr
    except Exception as exc:  # pragma: no cover
        return [{"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}]
    out: list[dict[str, Any]] = []
    for w in written:
        subject = str(w["subject"])
        if not w.get("report_due"):
            out.append({"subject": subject, "ok": True, "skipped": True,
                        "why": w.get("report_why")})
            continue
        try:
            res = cr.report_and_send(conn, ref_tag=subject, worker_key=worker_key)
            new_id = int(res.get("id") or 0)
            mark_reported(conn, "runtime_%s" % subject,
                          message_id=new_id, report_id=w.get("trace_id"))
            link = str(res.get("fault_link") or "")
            # THE SUPERSEDE PASS: retire the EARLIER `ask` messages for the SAME
            # occurrence, naming THIS message. `supersede_siblings` is the existing
            # rule from the ack plan — no second rule, no new column.
            superseded: list[dict[str, Any]] = []
            if link and new_id:
                try:
                    import report_ack as ra
                    superseded = ra.supersede_siblings(
                        conn, link, superseded_by=new_id,
                        note="a newer reminder for the same occurrence replaced it")
                except Exception as exc:
                    superseded = [{"ok": False,
                                   "error": "%s: %s" % (type(exc).__name__, exc)}]
            out.append({"subject": subject, "ok": bool(res.get("ok")),
                        "message_id": new_id, "title": res.get("title"),
                        "fault_link": link, "superseded": superseded})
        except Exception as exc:
            out.append({"subject": subject, "ok": False,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
    return out


def alarm_reason_text(problem: dict[str, Any]) -> str:
    """A one-line reason for a fault row: state + the measured value + its unit.

    THE UNIT IS PART OF THE REASON on purpose — "DEAD" without a number is a
    sentiment, "2560s since the newest event (stale after 300s)" is a reading.
    """
    name = str(problem.get("name") or "?")
    state = str(problem.get("state") or "?")
    secs = problem.get("seconds")
    unit = str(problem.get("unit") or "")
    if secs is None:
        return "%s is %s: %s" % (name, state, unit or "no measurable evidence")
    return "%s is %s: %.0fs %s" % (name, state, float(secs), unit)


# ---------------------------------------------------------------------------
# the structural promise: NO kill path (proved over the AST, not the text)
# ---------------------------------------------------------------------------

FORBIDDEN_CALL_NAMES = (
    ".kill", ".terminate", ".killpg", "taskkill", "pkill", ".popen",
    "terminateprocess", "start-process", "os.system",
)

# THE CHECKER'S OWN NAMES, excluded so the check does not flag itself.
# MEASURED, and this is the THIRD recurrence of the trap: `main()` calls
# `no_kill_path()` and the name CONTAINS `kill`, so the walk reported
# `['no_kill_path (line 463)']` — the checker flagging its own call. A detector
# whose name contains the forbidden token must name itself as an exception.
_SELF_CALL_NAMES = ("no_kill_path", "_no_kill_path", "_assert_no_kill_path")


def no_kill_path() -> list[str]:
    """This module must contain NO kill/terminate/spawn CALL. Offenders returned.

    A CHECK OVER THE PARSE TREE, not a regex over the text. MEASURED (third
    recurrence of this trap): a text scan matches the docstring ABOVE, which
    NAMES `kill`/`terminate` in order to forbid them. Prose cannot create an AST
    `Call` node, so walking the tree is the precise form and the comment stays
    free. Same rule as `watchdog_health._assert_no_kill_path`.

    AN AST WALK IS NECESSARY BUT NOT SUFFICIENT: the module's OWN checker is a
    `Call` named `no_kill_path`, which contains `kill`. Names in
    `_SELF_CALL_NAMES` are therefore skipped EXPLICITLY, because a detector that
    flags itself is never green and would be weakened until it was useless.
    """
    import ast
    src = (Path(__file__).resolve()).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        parts: list[str] = []
        while isinstance(func, ast.Attribute):
            parts.append("." + func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
        dotted = "".join(reversed(parts))
        low = dotted.lower()
        if low in _SELF_CALL_NAMES or low.lstrip(".") in _SELF_CALL_NAMES:
            continue
        if any(f in low for f in FORBIDDEN_CALL_NAMES):
            offenders.append("%s (line %d)" % (dotted, getattr(node, "lineno", 0)))
    return offenders


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="runtime trace connector")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--ensure", action="store_true",
                    help="register the term, the table row and the factor")
    ap.add_argument("--report", default=None, metavar="REF_TAG")
    ap.add_argument("--no-kill-check", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.no_kill_check:
            off = no_kill_path()
            print("kill-path offenders:", off or "NONE")
        if args.ensure:
            print(ensure_term(conn))
            print(ensure_table_registered(conn, "fault_factor_trace",
                                          description="runtime fault -> factor trace"))
            print(ensure_liveness_factor(conn))
        if args.report:
            import json as _json
            print(_json.dumps(report(conn, ref_tag=args.report), indent=2,
                              ensure_ascii=False, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
