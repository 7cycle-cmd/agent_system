"""watchdog_health.py — a LIVENESS UNIT that a dead process cannot author.

WHY THIS EXISTS (measured 2026-09-24, plan `WATCHDOG.HEARTBEAT.SKILL`)
--------------------------------------------------------------------
The user: *"why i think we can apply skill for watchdog or heartbeat to make it
work again and become stronger"*. The measurements said the intuition was right:

  * the watchdog's last `watchdog_log` row is `2026-09-14 11:46:17` and the last
    `worker_heartbeat` row is `2026-09-14 11:35:11` — **10 days of silence** —
    yet nothing alarmed.
  * `worker_heartbeat.business_alive` has **distinct = 1** across every row, and
    `worker_heartbeat.pid` has **distinct = 1**.

THE DEFECT THIS FILE REMOVES
----------------------------
`business_alive` is computed IN-PROCESS and then written by that same process:

    worker_heartbeat_service.py:60   business_alive() = monotonic() - last_tick <= 30
    worker_heartbeat_service.py:128  INSERT ... 1 if business_alive else 0, os.getpid()
    worker_heartbeat_service.py:197  send_heartbeat(worker_id, snap_path, state.business_alive())

**A dead process cannot write a row saying it is dead.** So `business_alive` can
only ever report the last opinion of a process that was, at that instant, alive
to hold it. It is structurally incapable of returning the other value — the same
failure class `empty_detector_failure_class` records.

THE FIX: measure the WORLD, not the process's opinion of itself.

    seconds since the newest row for (table, worker)

is a fact ABOUT THE ROW'S AGE. It is computed AT READ TIME by whoever is asking,
so the entity being measured does not author its own verdict. If the writer dies,
the row simply stops getting younger, and the number grows without anyone's help.

ONE UNIT, TWO CONSUMERS. The same unit answers "is the helper alive?" and "is the
watchdog itself alive?" — the second is the user's "become stronger", because the
watchdog is the component that died unnoticed. No second mechanism is invented.

WHAT THIS FILE DOES NOT DO
--------------------------
It NEVER kills, restarts, or spawns anything. `helper_watchdog.py:651` records why:

    Never kill helpers here. Duplicate prevention is helper's own mutex +
    skip-start-if-healthy. Killing from adopt caused false downs.

So "two helpers are running" is reported as an ALARM with a unit, and the
decision stays with a human. Making a state VISIBLE is this file's whole job.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent


class HealthError(ValueError):
    """The liveness answer cannot be stated as asked."""


# THE UNITS. One row per thing whose liveness is worth knowing, each carrying:
#   table/ts_col/scope_col  where the evidence lives
#   unit                    what the number IS, naming its subject
#   stale_after_sec         when ALIVE becomes DEAD
#
# A MAPPING, not a chain of `if`s: a new liveness question is a row here and the
# readers follow. The unit STRING is the deliverable — a bare number of seconds
# cannot be audited, and `route_registry._assert_unit` / `done_chain.assert_units`
# refuse a unit that names no subject. This is the same rule, applied to time.
LIVENESS_UNITS: dict[str, dict[str, Any]] = {
    "watchdog": {
        # THE SOURCE IS THE PER-TICK HEARTBEAT FILE. THIS WAS WRONG TWICE, AND
        # BOTH ERRORS WERE MEASURED — which is why the source is asserted by a
        # proof rather than trusted.
        #
        # ERROR 1 (2026-09-24): the unit read `watchdog_log`, a TABLE written by
        # `watchdog.py:70` — a DIFFERENT component. It reported 9.66 days silent
        # while the real component was working 2.07 days ago.
        #
        # ERROR 2 (2026-09-29): the unit read `helper_watchdog_events.json`,
        # which `helper_watchdog.record_event` writes ONLY on a TRANSITION. A
        # healthy watchdog writes NO event while the helper stays up, so the unit
        # reported DEAD for **166313.7s (46 hours)** while the loop was running
        # every 15s (measured: tick log `11:53:33Z` vs log line `19:55:24`, ~2
        # minutes apart). A wrong source and a real gap give the SAME verdict —
        # the `empty_detector_failure_class` defect, twice in one unit.
        #
        # THE FIX: read a sink written EVERY tick. `helper_watchdog.write_heartbeat`
        # writes `helper_watchdog_heartbeat.json` on the steady path. The two
        # obvious alternatives were MEASURED and rejected: the log's steady
        # cadence is ~302s (reconcile-only, depends on `chat_mode.json`
        # staleness), and the events file is FULL at `MAX_EVENTS = 80` so a
        # heartbeat there would evict every transition within ~6.7 hours.
        "source": "file",
        "path": "helper_watchdog_heartbeat.json",
        "ts_col": "ts",
        "ts_format": "iso_z",
        "unit": "seconds since the newest heartbeat in helper_watchdog_heartbeat.json",
        "stale_after_sec": 300,
        "ask": "Is the watchdog still writing its own heartbeat?",
    },
    "heartbeat": {
        "source": "table",
        "table": "worker_heartbeat",
        "ts_col": "heartbeat_at",
        "scope_col": "worker_id",
        # THE WRITER'S CLOCK, MEASURED NOT ASSUMED. `worker_heartbeat_service.py:128`
        # inserts `datetime('now','localtime')`, so the stored timestamps are LOCAL
        # (verified: a fresh row read `2026-09-24 13:33:10` while UTC was `05:33`).
        # Comparing them against bare `julianday('now')` (UTC) produced a NEGATIVE
        # age, which compares `<= stale_after_sec` and so read a just-started
        # component as ALIVE. The modifier is declared here, beside the unit, so
        # the clock is part of the declaration rather than a hidden assumption.
        "now_modifier": "localtime",
        "unit": "seconds since this worker's newest heartbeat row",
        "stale_after_sec": 900,
        "ask": "Is the worker still reporting a heartbeat?",
    },
}

# The alarm's unit, declared once so the reader and the proof cannot disagree.
DUPLICATE_HELPER_UNIT = "count of live mouse_spot_helper.py processes"
HELPER_SCRIPT = "mouse_spot_helper.py"
# THE WATCHDOG'S OWN DUPLICATE UNIT (added 2026-09-29, plan S2).
#
# MEASURED 2026-09-29: the keep-alive tick reported `pids=[30168, 42048]` for the
# watchdog, and NOTHING alarmed — `DUPLICATE_HELPER_UNIT` watches the HELPER only.
# MEASURED further, and this is why the unit must exclude stubs: pid 42048's
# `ExecutablePath` is `.venv\Scripts\pythonw.exe` and EQUALS the first token of
# its own command line, so it is a venv LAUNCHER STUB, not a second watchdog. The
# real interpreter is pid 30168 (`...\Python313\pythonw.exe`). So the live state
# is ONE watchdog, and a unit that counted raw command-line matches would have
# fired a FALSE alarm on every tick — the same defect `is_launcher_stub` was
# written to remove for the helper (2026-09-26).
DUPLICATE_WATCHDOG_UNIT = "count of live helper_watchdog.py processes"
WATCHDOG_SCRIPT = "helper_watchdog.py"

# ---- THE DRAFT-CONVERSATION UNIT (added 2026-09-29, plan S4) ----------------
#
# THE WATCHDOG'S FIRST DATA UNIT. Every unit above measures a PROCESS; this one
# measures a BUSINESS STATE (`chat_main.status`). The failure modes differ, and
# the proof covers all four: a FALSE draft (a historical row firing), a MISSED
# draft (a new row not firing), a REPEATED draft (the same row firing twice), and
# a STALE watermark (a restart losing the mark).
#
# WHY A WATERMARK, AND WHY IT IS IN THE DB. MEASURED 2026-09-29: `status='draft'`
# was **93 of 93** rows — the WHOLE table — because S3's backfill set every
# conversation to `draft`. A per-draft unit with no watermark would fire **93**
# escalations on its first tick. So only drafts created AFTER the watchdog first
# observed are noticed, and the mark is stored in `settings` (NOT in memory), so
# a restart cannot re-flood — the same rule `runtime_trace.should_report` follows.
DRAFT_WATERMARK_KEY = "watchdog.draft_noticed_since"
DRAFT_UNIT = "count of draft conversations created since the watchdog watermark"
DRAFT_GROUP_ID = "runtime_draft_since_watermark"


def ensure_draft_watermark(conn: sqlite3.Connection, *,
                           now: str | None = None) -> dict[str, Any]:
    """Set the draft watermark ONCE, on first observation. Idempotent.

    THE INITIALISATION IS `now()` AT FIRST RUN (the PM's ruling, option (a)), not
    the first draft's `created_at`. Option (b) has a chicken-and-egg defect: if
    the first new draft appears before the watchdog initialises, the mark would
    be set to THAT draft's `created_at` and the draft would never be noticed.

    THE CONSEQUENCE, stated so it is not a surprise: a draft created BEFORE the
    watchdog's first run is treated as HISTORY and is never noticed. So S4's
    rollout must start the watchdog once (writing the mark) BEFORE a test draft
    is created.

    Returns `{ok, created, watermark}`. `created=False` means it already existed,
    so a re-run never moves the mark forward.
    """
    import db_schema as ds
    existing = ds.get_setting(conn, DRAFT_WATERMARK_KEY)
    if existing not in (None, ""):
        return {"ok": True, "created": False, "watermark": str(existing)}
    mark = str(now) if now is not None else str(
        conn.execute("SELECT datetime('now')").fetchone()[0])
    ds.set_setting(conn, DRAFT_WATERMARK_KEY, mark)
    return {"ok": True, "created": True, "watermark": mark}


def draft_since_watermark(conn: sqlite3.Connection, *,
                          now: str | None = None) -> dict[str, Any]:
    """How many draft conversations appeared SINCE the watermark? An ALARM, never
    an action.

    THE UNIT IS A COUNT, not a per-draft row (the PM's ruling). One count means
    ONE escalation, and the per-draft dedup is automatic: a draft counted once
    stays counted, so a second tick reports the same number and the EXISTING
    `should_report` cooldown suppresses the repeat.

    IT INITIALISES THE WATERMARK IF ABSENT, so the first call is safe: it writes
    `now()` and reports 0 (nothing can be newer than a mark set at this instant).

    THE MESSAGE NAMES THE ROWS when the count is small, so a human does not have
    to go looking — the PM's usability condition.
    """
    import db_schema as ds
    mark = ensure_draft_watermark(conn, now=now)
    since = str(mark["watermark"])
    rows = [dict(r) for r in conn.execute(
        "SELECT id, session_id, created_at FROM chat_main "
        "WHERE status='draft' AND created_at > ? ORDER BY created_at", (since,))]
    n = len(rows)
    named = ", ".join("#%d" % r["id"] for r in rows[:10])
    return {"ok": n == 0, "count": n, "name": "draft_since_watermark",
            "unit": DRAFT_UNIT, "expected": "0 new drafts",
            "watermark": since, "rows": rows,
            "cite_ref": "watchdog_health.py:%d" % _line_of("DRAFT_UNIT"),
            "why": ("%d draft conversation(s) created since %s%s"
                    % (n, since, (": " + named) if named else ""))}

# THE FIELD SOURCE, DERIVED — NOT A SECOND COPY.
#
# `skill_registrar.fields_from_seed` reads a module's `DIMENSIONS` as
# `(field_name, question, hard_rule, mandatory)`. Both new skills declare
# `field_seed: watchdog_health`, because this module is what they must answer.
# The liveness questions are ALREADY declared once, in `LIVENESS_UNITS`, so this
# is a DERIVED view: add a row above and it appears here, with no second edit to
# forget. `DUPLICATE_HELPER_UNIT` is included because "more than one helper" is a
# liveness question too, and leaving it out would make the contract silent about
# the state that was actually observed live (2 helpers).
DIMENSIONS: tuple[tuple[str, str, str, bool], ...] = (
    tuple(
        (name, str(spec["ask"]),
         "must be answered with a measurable value in the unit: %s (stale after "
         "%ds)" % (spec["unit"], spec["stale_after_sec"]), True)
        for name, spec in LIVENESS_UNITS.items()
    )
    + (("duplicate_helper",
        "Is more than one helper process running?",
        "must be answered with a measurable value in the unit: %s (expected: "
        "at most 1)" % DUPLICATE_HELPER_UNIT, True),)
    + (("duplicate_watchdog",
        "Is more than one watchdog process running?",
        "must be answered with a measurable value in the unit: %s (expected: "
        "at most 1)" % DUPLICATE_WATCHDOG_UNIT, True),)
    + (("draft_since_watermark",
        "Has a draft conversation appeared since the watchdog watermark?",
        "must be answered with a measurable value in the unit: %s (expected: "
        "0)" % DRAFT_UNIT, True),)
)

# A liveness state is ONE of these. NEVER is deliberately distinct from DEAD:
# "no row has EVER been written" is a different fault from "the rows stopped",
# and collapsing them loses the distinction that matters when diagnosing.
ALIVE, DEAD, NEVER = "ALIVE", "DEAD", "NEVER"


def unit_of(name: str) -> str:
    """The unit a liveness question is measured in. '' for an unknown name."""
    return str(LIVENESS_UNITS.get(str(name or "").strip(), {}).get("unit", ""))


def assert_units() -> list[str]:
    """Every liveness unit must NAME ITS SUBJECT. Returns the problems.

    The rule is `done_chain.assert_units`' and `route_registry._assert_unit`'s,
    applied to a time unit: `seconds` alone says nothing, `seconds since this
    worker's newest heartbeat row` says what is being aged.
    """
    problems: list[str] = []
    for name in LIVENESS_UNITS:
        u = unit_of(name)
        if not u:
            problems.append("liveness %r has no unit" % name)
            continue
        low = u.lower()
        if low in ("seconds", "time", "age", "duration", "count"):
            problems.append("liveness %r unit %r names NO SUBJECT" % (name, u))
        elif not re.search(r"\b(since|of|for)\b", low):
            # A unit must connect the measure to a subject, not merely contain
            # words: "seconds heartbeat" is not a unit.
            problems.append("liveness %r unit %r names no SUBJECT RELATION"
                            % (name, u))
    return problems


def freshness(conn: sqlite3.Connection, name: str, worker_id: int, *,
              now: str | None = None) -> dict[str, Any]:
    """How old is this source's newest record? DERIVED AT READ TIME.

    TWO SOURCE KINDS, because the components record in two places and pointing at
    the wrong one was a MEASURED error of 7.6 days (see `LIVENESS_UNITS`):
      * `source: "table"` — a row in the DB, read with SQL julianday arithmetic
      * `source: "file"`  — a JSON list of events, read and parsed

    `now` exists so a FIXTURE's staleness is deterministic: without it a proof
    would have to sleep, and a test that waits on wall-clock time is a test whose
    result depends on the machine. Live callers omit it.

    Returns `{ok, name, unit, state, seconds, count, cite_ref, why}`.
    `seconds` is None ONLY when nothing was ever recorded (state NEVER) — never as
    a stand-in for "unknown", because a None that means two things makes the
    caller's branch unreadable (`no_null` standard).
    """
    spec = LIVENESS_UNITS.get(str(name or "").strip())
    if spec is None:
        raise HealthError("unknown liveness question %r — add a LIVENESS_UNITS "
                          "row rather than guessing a unit" % name)
    kind = str(spec.get("source") or "table")
    if kind == "file":
        count, newest = _newest_in_file(spec)
    elif kind == "table":
        ts, scope = spec["ts_col"], spec["scope_col"]
        # THE CLOCK MUST MATCH THE ONE THAT WROTE THE ROW. MEASURED 2026-09-24:
        # `worker_heartbeat_service.py:128` inserts `datetime('now','localtime')`
        # — LOCAL time (13:33) — while SQLite's bare `julianday('now')` is UTC
        # (05:33). Subtracting them produced `age_seconds = -28762` (a NEGATIVE
        # age) for a row written seconds earlier. A negative age then compares
        # `<= stale_after_sec` and reads as ALIVE, so the defect made a JUST
        # STARTED component look healthy for the wrong reason.
        #
        # The `now` modifier is therefore DECLARED per source, not assumed. A
        # fixture that inserts `julianday('now', '-10 seconds')` is already in the
        # same UTC frame as bare `julianday('now')`, which is why the fixture and
        # the declaration use the SAME modifier string.
        now_mod = str(spec.get("now_modifier") or "")
        now_expr = "julianday(?)" if now is not None else \
            ("julianday('now'%s)" % (", '%s'" % now_mod if now_mod else ""))
        # SQLite does the arithmetic so ONE implementation serves the live DB and
        # a fixture: julianday difference in days, times 86400. It is computed
        # HERE rather than by `_age_seconds` because SQLite stores a TIMESTAMP
        # column as EITHER text or a julian number depending on how it was
        # written (MEASURED: a fixture's `julianday(...)` insert stored
        # `2461307.72`, while the live rows store text) — so the age must come
        # from SQLite's own comparison, which handles both.
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(%s) AS newest, "
            "(%s - julianday(MAX(%s))) * 86400.0 AS age "
            "FROM %s WHERE %s = ?" % (ts, now_expr, ts, spec["table"], scope),
            ((str(now),) if now is not None else ()) + (worker_id,)).fetchone()
        count, newest = int(row["n"] or 0), row["newest"]
        if count and newest not in (None, ""):
            seconds = float(row["age"] or 0.0)
            # A NEGATIVE AGE IS A CLOCK MISMATCH, NOT A HEALTHY ROW. MEASURED
            # 2026-09-24: a UTC-vs-local mismatch gave `-28762`, which compares
            # `<= stale_after_sec` and reads ALIVE. Refusing it turns an invisible
            # defect into a loud one, the rule `_age_seconds` already applies to
            # an unparseable timestamp.
            if seconds < 0:
                raise HealthError(
                    "%s: age is NEGATIVE (%.0fs) for the newest %s — the row's "
                    "clock and the comparison clock disagree; declare a "
                    "`now_modifier` rather than reading a future timestamp as "
                    "healthy" % (name, seconds, spec["table"]))
            state = (ALIVE if seconds <= float(spec["stale_after_sec"])
                     else DEAD)
            src_desc = "%s row" % spec["table"]
            return {"ok": state == ALIVE, "name": name, "unit": spec["unit"],
                    "state": state, "seconds": seconds, "count": count,
                    "stale_after_sec": spec["stale_after_sec"],
                    "cite_ref": "watchdog_health.py:%d"
                                % _line_of("LIVENESS_UNITS"),
                    "why": "%.0fs since the newest %s (stale after %ds)"
                           % (seconds, src_desc, spec["stale_after_sec"])}
    else:
        raise HealthError("liveness %r has an unknown source kind %r"
                          % (name, kind))

    if count == 0 or newest in (None, ""):
        state, seconds = NEVER, None
    else:
        seconds = _age_seconds(newest, now)
        state = ALIVE if seconds <= float(spec["stale_after_sec"]) else DEAD
    src_desc = ("%s row" % spec["table"] if kind == "table"
                else str(spec["path"]))
    why = ("no record has EVER been written to %s" % src_desc if state == NEVER
           else "%.0fs since the newest %s (stale after %ds)"
           % (seconds, src_desc, spec["stale_after_sec"]))
    return {"ok": state == ALIVE, "name": name, "unit": spec["unit"],
            "state": state, "seconds": seconds, "count": count,
            "stale_after_sec": spec["stale_after_sec"],
            "cite_ref": "watchdog_health.py:%d" % _line_of("LIVENESS_UNITS"),
            "why": why}


def _newest_in_file(spec: dict[str, Any]) -> tuple[int, str | None]:
    """The event count and newest `ts` in a JSON event file. (0, None) if absent.

    A MISSING FILE IS `NEVER`, NOT AN ERROR: the component has not recorded
    anything, which is precisely the state a liveness check exists to report.
    Raising instead would make the check unusable on a fresh machine — and worse,
    it would turn "no evidence" into "cannot answer", hiding the finding.
    """
    p = BASE_DIR / str(spec["path"])
    if not p.is_file():
        return 0, None
    # THE EXCEPTION IS NARROW ON PURPOSE. MEASURED, and this bit me: a bare
    # `except Exception: return 0, None` around `json.loads` swallowed a
    # NameError (the module used `json` before it imported it), so the unit
    # reported `NEVER` — "no events were ever written" — for a file holding 80
    # events. A broken extractor and a real gap BOTH returned zero, which is the
    # `empty_detector_failure_class` defect reproduced in this module's own file
    # reader. Only a DECODE failure may mean "unreadable"; anything else is a bug
    # and must surface.
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return 0, None
    events = data if isinstance(data, list) else data.get("events", [])
    if not isinstance(events, list) or not events:
        return 0, None
    tss = [str(e.get(spec["ts_col"]) or "") for e in events
           if isinstance(e, dict)]
    tss = [t for t in tss if t]
    if not tss:
        return 0, None
    return len(events), max(tss)


def _age_seconds(newest: str, now: str | None) -> float:
    """Seconds between `newest` and `now`. Accepts an ISO-ish timestamp.

    `now` is a PARAMETER so a fixture is deterministic; live callers pass none and
    the current UTC time is used. Parsing is tolerant on purpose (`Z`, a space,
    a fractional part) because the two sources spell the timestamp differently and
    a strict parser would fail on one of them — a parse failure returning 0 would
    read as "brand new", which is the false-ALIVE this module exists to remove.
    """
    import datetime as _dt
    fmt_in = "%Y-%m-%dT%H:%M:%S"
    cand = str(newest).strip().replace("Z", "").replace(" ", "T")
    if "." in cand:
        cand = cand.split(".", 1)[0]
    try:
        a = _dt.datetime.strptime(cand[:19], fmt_in).replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        raise HealthError("cannot parse timestamp %r — refusing to guess an age "
                          "(a 0 here would read as brand new)" % newest)
    if now is None:
        b = _dt.datetime.now(_dt.timezone.utc)
    else:
        nc = str(now).strip().replace("Z", "").replace(" ", "T")
        if "." in nc:
            nc = nc.split(".", 1)[0]
        # `now="now"` means "the current time", so a caller can ask for the live
        # answer while a fixture still supplies a fixed instant.
        if nc.lower() == "now":
            b = _dt.datetime.now(_dt.timezone.utc)
        else:
            b = _dt.datetime.strptime(nc[:19], fmt_in).replace(
                tzinfo=_dt.timezone.utc)
    return (b - a).total_seconds()


def _line_of(symbol: str) -> int:
    """The line of a module-level symbol, so a `cite_ref` is DERIVED not typed."""
    src = Path(__file__).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(src, 1):
        if line.startswith(symbol):
            return i
    return 1


def diagnose(conn: sqlite3.Connection, worker_id: int, *,
             now: str | None = None) -> dict[str, Any]:
    """Every liveness question for one worker. Reports ALL of them, never raises.

    A report that lists only failures cannot show that the check was run — the
    same rule `done_chain.walk_chain` follows.
    """
    units = [freshness(conn, n, worker_id, now=now) for n in LIVENESS_UNITS]
    dead = [u["name"] for u in units if u["state"] != ALIVE]
    return {"ok": not dead, "worker_id": worker_id, "units": units,
            "dead": dead}


# ---------------------------------------------------------------------------
# The duplicate-helper ALARM
# ---------------------------------------------------------------------------

def live_script_pids(script: str, pids: list[int] | None = None, *,
                     ps_output: str | None = None) -> list[int]:
    """The live process ids whose command line names `script`, EXCLUDING stubs.

    THE ONE READER, parameterised by script. `live_helper_pids` and the watchdog
    duplicate check both delegate here, so the stub-exclusion rule cannot drift
    between them — a second copy is how one of them would keep counting a venv
    launcher stub while the other stopped.

    `pids` lets a caller (or a proof) supply a deterministic list instead of
    asking the OS — a proof that asserts on the real process table is a proof
    whose result changes when an unrelated program starts.

    `ps_output` is the same idea one level down: the raw text to parse. It exists
    so the PARSER is testable without spawning a process.

    ------------------------------------------------------------------
    MEASURED DEFECT (2026-09-26) — the alarm counted a venv LAUNCHER STUB.

    The alarm reported **2** live helpers and said "two helpers share the port and
    neither is authoritative". MEASURED: exactly ONE process owned port 18765, and
    the second was a venv launcher stub. The three `pythonw` pairs, MEASURED:

        pid=27896  threads=1  handles=55  cpu=0.02s  image=.venv\\Scripts\\pythonw.exe
        pid=27116  threads=22 handles=355 cpu=59.38s image=…\\Python313\\pythonw.exe  <- owns 18765
        pid=28972  threads=1  handles=55  cpu=0s
        pid=30476  threads=35 handles=357 cpu=15.23s
        pid=33160  threads=1  handles=55  cpu=0.02s
        pid=7684   threads=3  handles=141 cpu=6.19s

    This is how a `uv`-created venv works on Windows (`pyvenv.cfg`: `uv = 0.12.13`):
    `.venv\\Scripts\\pythonw.exe` is a **251 KB LAUNCHER** whose only job is to start
    the real interpreter and wait. So ONE component is TWO processes, and the stub's
    command line is a COPY of the real one's — MEASURED, byte-identical:

        pid=27896  CMD: "…\\.venv\\Scripts\\pythonw.exe" mouse_spot_helper.py
        pid=27116  CMD: "…\\.venv\\Scripts\\pythonw.exe" mouse_spot_helper.py

    **The command line CANNOT discriminate them.** The image path CAN: the stub was
    launched as itself, so its `ExecutablePath` EQUALS the first token of its own
    command line; the real interpreter's does not.

    CONVERGENT EVIDENCE, and the repo already knew: `mouse_spot_helper.pid` names
    **27116** and `helper_watchdog.pid` names **30476** — the interpreters, not the
    stubs.

    THE PARSER IS BACKWARD COMPATIBLE. A 3-field line is `pid|exe|cmdline`; a
    1-field line is the legacy `pid|cmdline`, which is still accepted so every
    existing caller and `_proof_watchdog_health.py` keep working.
    """
    if pids is not None:
        return sorted(int(p) for p in pids)
    if ps_output is None:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object { "
                 "\"$($_.ProcessId)|$($_.ExecutablePath)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=20)
            ps_output = r.stdout or ""
        except Exception as exc:  # pragma: no cover - depends on the host
            raise HealthError("cannot read the process table: %s" % exc)
    out: list[int] = []
    for line in ps_output.splitlines():
        if script not in line:
            continue
        parts = line.split("|", 2)
        head = parts[0].strip()
        if not head.isdigit():
            continue
        if is_launcher_stub(parts[1] if len(parts) >= 2 else "",
                            parts[2] if len(parts) >= 3 else ""):
            continue
        out.append(int(head))
    return sorted(out)


def live_helper_pids(pids: list[int] | None = None, *,
                     ps_output: str | None = None) -> list[int]:
    """The live `mouse_spot_helper.py` process ids, EXCLUDING launcher stubs.

    DELEGATES to `live_script_pids` — ONE reader, so the stub rule cannot drift.
    """
    return live_script_pids(HELPER_SCRIPT, pids, ps_output=ps_output)


def live_watchdog_pids(pids: list[int] | None = None, *,
                       ps_output: str | None = None) -> list[int]:
    """The live `helper_watchdog.py` process ids, EXCLUDING launcher stubs.

    MEASURED 2026-09-29: the raw command-line match found TWO pids for the
    watchdog, but one was a venv launcher stub. This reader returns the REAL
    watchdogs only, so the duplicate alarm cannot fire on a stub.
    """
    return live_script_pids(WATCHDOG_SCRIPT, pids, ps_output=ps_output)


def is_launcher_stub(executable_path: str, command_line: str) -> bool:
    """Did this process launch ITSELF, i.e. is it a venv launcher stub?

    A venv `python[w].exe` is a launcher that starts the real interpreter. The
    launcher was started as itself, so:

        ExecutablePath == first token of CommandLine

    The real interpreter was started BY the launcher, so its `ExecutablePath` is
    the interpreter while its command line still names the launcher — the two
    differ.

    MEASURED, the discriminating pair:

        stub  pid=27896  exe=.venv\\Scripts\\pythonw.exe        cmdline=".venv\\Scripts\\pythonw.exe" mouse_spot_helper.py
        real  pid=27116  exe=…\\Python313\\pythonw.exe          cmdline=".venv\\Scripts\\pythonw.exe" mouse_spot_helper.py

    A missing either side (a legacy 1-field line) is NOT treated as a stub, so an
    unknown shape counts as REAL — the alarm fails LOUD in the fewest cases, which
    is the right direction for an alarm.
    """
    exe = str(executable_path or "").strip().strip('"')
    cmd = str(command_line or "").strip()
    if not exe or not cmd:
        return False
    first = cmd.split(" ", 1)[0].strip().strip('"')
    if not first:
        return False
    try:
        return os.path.normcase(os.path.abspath(exe)) == os.path.normcase(
            os.path.abspath(first))
    except Exception:  # noqa: BLE001 - a malformed path is not a stub
        return False


def duplicate_helper_alarm(pids: list[int] | None = None, *,
                           ps_output: str | None = None) -> dict[str, Any]:
    """Is more than one helper running? An ALARM, never a kill.

    MEASURED why this is worth an alarm rather than a warning line:
    `helper_watchdog.py:883` already enumerates the helpers and LOGS
    `Helper process(es) already present pids=[...]`, but it does that only on the
    START path and treats >1 as a reason to WAIT, not as a state to report. With
    the watchdog dead (measured: 10 days silent), nothing reports it at all.

    THIS FUNCTION KILLS NOTHING. `helper_watchdog.py:651` records that killing
    from `adopt` caused FALSE DOWNS. `_assert_no_kill_path` in the proof asserts
    this module contains no kill call, so the restraint cannot be removed quietly.
    """
    live = live_helper_pids(pids, ps_output=ps_output)
    n = len(live)
    return {"ok": n <= 1, "count": n, "pids": live, "unit": DUPLICATE_HELPER_UNIT,
            "expected": "at most 1",
            "cite_ref": "watchdog_health.py:%d" % _line_of("DUPLICATE_HELPER_UNIT"),
            "why": ("%d live %s processes (pids=%s); more than one means two "
                    "helpers share the port and neither is authoritative"
                    % (n, HELPER_SCRIPT, live))}


def duplicate_watchdog_alarm(pids: list[int] | None = None, *,
                             ps_output: str | None = None) -> dict[str, Any]:
    """Is more than one WATCHDOG running? An ALARM, never a kill.

    MEASURED 2026-09-29: the keep-alive tick reported `pids=[30168, 42048]` for the
    watchdog and NOTHING alarmed, because `DUPLICATE_HELPER_UNIT` watches the
    helper only. Two watchdogs race for the same mutex
    (`Local\\AgentSystemHelperWatchdog_v4`) and neither is authoritative.

    IT EXCLUDES LAUNCHER STUBS, and that is the whole point of the measurement:
    pid 42048 is a venv launcher stub, so the live state is ONE watchdog. A unit
    that counted raw command-line matches would fire a FALSE alarm every tick.

    THIS FUNCTION KILLS NOTHING. `helper_watchdog.py:651` records that killing
    from `adopt` caused FALSE DOWNS. `_assert_no_kill_path` asserts this module
    contains no kill call, so the restraint cannot be removed quietly.
    """
    live = live_watchdog_pids(pids, ps_output=ps_output)
    n = len(live)
    return {"ok": n <= 1, "count": n, "pids": live, "name": "duplicate_watchdog",
            "unit": DUPLICATE_WATCHDOG_UNIT, "expected": "at most 1",
            "cite_ref": "watchdog_health.py:%d"
                        % _line_of("DUPLICATE_WATCHDOG_UNIT"),
            "why": ("%d live %s processes (pids=%s); more than one means two "
                    "watchdogs race for the same mutex and neither is "
                    "authoritative" % (n, WATCHDOG_SCRIPT, live))}


# ---------------------------------------------------------------------------
# The alarm as a STRUCTURED reason, so the EXISTING cycle can consume it
# ---------------------------------------------------------------------------

def alarm_reason(result: dict[str, Any]) -> dict[str, Any]:
    """Turn a failed liveness/alarm result into `{ring, unit, observed, expected}`.

    THE SHAPE IS NOT A STYLE CHOICE. `done_chain.propose_factor_from_refusal`
    REFUSES an unstructured reason, because `factor_template_growth` records that
    deriving factors from `proof_run.failure_reason` produced GARBAGE
    (`oracle_999999999999999`) — that field holds a TEST PAYLOAD, not a missing
    property. Returning the four keys here means the EXISTING collector and the
    EXISTING worked-example writer are reused, instead of a second pair invented.
    """
    unit = str(result.get("unit") or "")
    if not unit:
        raise HealthError("an alarm reason needs a unit that names its subject")
    name = str(result.get("name") or ("duplicate_helper"
                                      if "pids" in result else "liveness"))
    observed = result.get("seconds") if result.get("seconds") is not None \
        else result.get("count")
    return {"ring": name, "unit": unit, "observed": observed,
            "expected": str(result.get("expected")
                            or ("at most %ss" % result.get("stale_after_sec"))
                            or "ALIVE"),
            "cite_ref": str(result.get("cite_ref") or "")}


def measure_routes(conn: sqlite3.Connection, worker_id: int = 1, *,
                   now: str | None = None, apply: bool = False
                   ) -> dict[str, Any]:
    """Derive each watchdog route's health from a MEASUREMENT, then store it.

    WHY THIS EXISTS — QC-07. `declare_route` leaves `health='UNKNOWN'` (the DDL
    default), and a route whose health was never measured is a claim, not a route.
    MEASURED: after `seed_routes` the three new routes read `UNKNOWN` while the
    four older ones read `OK` — the difference being that the old ones had been
    measured by an earlier session.

    IT REUSES `route_registry.derive_health` and `record_health`, deliberately: a
    second derivation would be a second answer to "is this route healthy?", and
    `derive_health` already encodes the TIER priority (a dangling end dominates).
    This function's only job is to SUPPLY the counts, from units already measured.

    `apply=False` computes and reports and writes NOTHING — a dry run stays dry.
    """
    import route_registry as rr

    liveness = {n: freshness(conn, n, worker_id, now=now)
                for n in LIVENESS_UNITS}
    dup = duplicate_helper_alarm()
    # `unresolved_endpoints`: how many liveness questions answered non-ALIVE. A
    # route whose `to` side is a dead process does not resolve, so the tier rule
    # in `derive_health` (NOWHERE dominates) applies without a new state.
    unresolved = sum(1 for u in liveness.values() if u["state"] != ALIVE)
    if dup["count"] > 1:
        unresolved += 1
    # `forward_call_sites`: how many liveness units are ALIVE. A module with no
    # live call site is NEVER-reached, which is a different fault from an
    # unresolved endpoint.
    forward = sum(1 for u in liveness.values() if u["state"] == ALIVE)
    drifted = 0
    try:
        import terminology_cite as tc
        # The declaration stays valid while the cited file still exists; a
        # vanished cite is the only drift THIS module can measure honestly.
        drifted = 0 if all(
            tc.verify_cite_ref(str(r["cite_ref"]))[0]
            for r in rr.routes_of(conn)
            if str(r["route_key"]).startswith(("watchdog_", "worker_", "helper_"))
        ) else 1
    except Exception:
        drifted = 1

    out: list[dict[str, Any]] = []
    for row in rr.routes_of(conn):
        key = str(row["route_key"])
        if not key.startswith(("watchdog_", "worker_", "helper_")):
            continue
        d = rr.derive_health(unresolved_endpoints=unresolved,
                             forward_call_sites=forward,
                             reverse_call_sites=forward, drifted_refs=drifted)
        out.append({"route_key": key, "health": d["health"], "count": d["count"],
                    "measured": d["measured"], "unit": d["unit"]})
        if apply:
            rr.record_health(conn, key, health=d["health"],
                             health_count=d["count"], commit=False)
    if apply:
        conn.commit()
    states = {e["route_key"]: e["health"] for e in out}
    return {"ok": True, "applied": bool(apply), "routes": out, "states": states,
            "liveness": {n: liveness[n]["state"] for n in liveness},
            "duplicate_helpers": dup["count"],
            "any_not_ok": any(h != "OK" for h in states.values())}


def alarm_once(conn: sqlite3.Connection, worker_id: int = 1, *,
               now: str | None = None, notify: bool = True,
               persist: bool = True) -> dict[str, Any]:
    """One pass: measure, RECORD, alarm, and RETURN. Starts nothing, kills nothing.

    THE WATCHDOG-OF-THE-WATCHDOG (the mechanism chosen at approval, plan §2
    Step 1 option b), implemented as a FUNCTION rather than a resident process.

    WHY A FUNCTION AND NOT A LOOP: this plan's forbidden list says "killing,
    restarting, or spawning ANY process", and a `while True:` in this module would
    be exactly that. A callable entry point can be run by ANY scheduler — a
    Windows scheduled task, an existing host loop, or a human — so the DECISION
    about autostart stays where it belongs, and this module keeps its single job.

    ITS OWN LIVENESS IS THE SAME UNIT. `freshness(conn, 'watchdog', ...)` is the
    watchdog's own age, so the component that died unnoticed (measured: 10 days
    silent) now reports on itself through the ONE unit the helper uses too.

    `notify` is best-effort and LAST: the returned reasons exist whether or not a
    notification can be delivered, so a failed notify cannot lose the finding —
    the rule `worker_help.py` follows ("DB write FIRST so the audit trail exists
    even if notify fails").
    """
    diag = diagnose(conn, worker_id, now=now)
    dup = duplicate_helper_alarm()
    dup_wd = duplicate_watchdog_alarm()
    # THE DRAFT UNIT (added 2026-09-29, plan S4). It measures DATA, not a process,
    # and it INITIALISES its watermark on first call, so the first run reports 0.
    draft = draft_since_watermark(conn, now=now)
    problems: list[dict[str, Any]] = [u for u in diag["units"]
                                      if u["state"] != ALIVE]
    if not dup["ok"]:
        problems.append(dup)
    # THE WATCHDOG'S OWN DUPLICATE (added 2026-09-29, plan S2). MEASURED: the tick
    # reported `pids=[30168, 42048]` and nothing alarmed. The unit EXCLUDES the
    # venv launcher stub, so the live state (ONE real watchdog) stays silent.
    if not dup_wd["ok"]:
        problems.append(dup_wd)
    # A NEW DRAFT IS A PROBLEM TO REPORT, never an action to take.
    if not draft["ok"]:
        problems.append(draft)
    # ---- THE TRACE WRITE, FIRST (plan RUNTIME.TRACE.CYCLE, QC-01) ----------
    # MEASURED before this: `alarm_once` measured, then called `try_notify` and
    # wrote NOTHING. A toast is best-effort and non-durable, so a real fault
    # (measured: watchdog WEDGED for 2560s) left no row — `fault_factor_trace`
    # had 0 rows and no writer in the whole repo. The DB write now happens
    # BEFORE the notify, because the audit trail must exist even when notify
    # fails (`worker_help.py`'s rule).
    traced: list[dict[str, Any]] = []
    if problems and persist:
        try:
            import runtime_trace as _rt
            _rt.ensure_term(conn, commit=False)
            _rt.ensure_table_registered(conn, "fault_factor_trace",
                                        commit=False)
            _rt.ensure_liveness_factor(conn, commit=False)
            conn.commit()
            traced = _rt.record_alarm(
                conn, {"problems": problems,
                       "checked": (len(diag["units"])
                                   + (0 if dup["ok"] else 1)
                                   + (0 if dup_wd["ok"] else 1)
                                   + (0 if draft["ok"] else 1)),
                       "states": {u["name"]: u["state"] for u in diag["units"]}},
                worker_id=worker_id)
        except Exception as exc:
            # A trace failure must NOT lose the alarm: the RETURNED reasons exist
            # either way, and the failure is reported so it is visible.
            traced = [{"ok": False, "error": "%s: %s"
                       % (type(exc).__name__, exc)}]
    notified = False
    if problems and notify:
        try:  # pragma: no cover - depends on the host
            import openclaw_bridge
            title = "watchdog_health: %d problem(s)" % len(problems)
            body = "; ".join(str(p.get("why") or "") for p in problems)[:400]
            notified = bool(openclaw_bridge.try_notify(title, body))
        except Exception:
            notified = False
    return {"ok": not problems, "problems": problems,
            "states": {u["name"]: u["state"] for u in diag["units"]},
            "duplicate_helpers": dup["count"],
            "duplicate_watchdogs": dup_wd["count"],
            "drafts_since_watermark": draft["count"],
            "draft_watermark": draft["watermark"],
            "reasons": [alarm_reason(p) for p in problems],
            "traced": traced,
            "notified": notified}


def _assert_no_kill_path() -> list[str]:
    """This module must contain NO kill/terminate CALL. Returns the offenders.

    A CHECK OVER THE PARSE TREE, not a regex over the text. MEASURED, and my
    first version was wrong exactly as `/memories/repo/watchdog_rate_limit.md`
    already records for a different check:

        "Counting the STRING `take_shot=True` finds 6, because my own comment
         mentions it."

    A text scan flags the DOCSTRING above, which NAMES `taskkill` in order to
    explain why it is forbidden — so the check would report a violation for
    documenting the rule. Prose cannot create an AST `Call` node, so walking the
    tree is the precise form and the comment stays free.
    """
    import ast
    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    kill_names = {"kill", "terminate", "killall", "taskkill", "stop_process",
                  "terminateprocess"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = (fn.attr if isinstance(fn, ast.Attribute)
                else getattr(fn, "id", "") or "")
        if str(name).lower() in kill_names:
            bad.append("line %d: %s(" % (node.lineno, name))
    return bad


if __name__ == "__main__":  # pragma: no cover - a manual view
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
    conn.row_factory = sqlite3.Row
    print("unit problems:", assert_units())
    for wid in (1,):
        d = diagnose(conn, wid)
        print("worker %s: ok=%s dead=%s" % (wid, d["ok"], d["dead"]))
        for u in d["units"]:
            print("   %-10s %-6s %s" % (u["name"], u["state"], u["why"]))
    a = duplicate_helper_alarm()
    print("duplicate helper alarm:", a["why"])
    print("kill-path offenders (must be []):", _assert_no_kill_path())
    conn.close()
