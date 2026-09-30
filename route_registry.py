"""route_registry.py — a CONNECTION, declared with a unit so it can be proven.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "wire / connection is route, can manage by that"
    "A and B can register by 2 row, this is why to have terminology-register"
    "classify for everything"

THE PROBLEM IT ANSWERS: a connection failure had to be REDISCOVERED BY HAND
every time. MEASURED this session: `logic_generator` -> `question_flow`,
`field_tdd_rule` -> `oracle_for`, `prompt_registry` -> the judge instruction, and
`step_no` -> `questions[idx]` were each found by inspecting code. There was no
table in which a connection is DECLARED, so there was nothing to check.

WHY A NEW TABLE AND NOT AN EXISTING ONE — a STRUCTURAL test, not a preference:

  | test            | capability_binding                      | onto_link                     | a route needs |
  |-----------------|-----------------------------------------|-------------------------------|---------------|
  | has a TO side   | NO (`capability_id` + ONE `subject_ref`)| yes (`from_concept_id`+`to_`) | YES           |
  | `status` means  | DECLARED/CONFIRMED/REJECTED (APPROVAL)  | none                          | HEALTH        |
  | has a `unit`    | NO                                      | NO                            | YES           |

  MEASURED: `api_registry` and `function_registry` both have `capability_id`
  `distinct=1` — every row points at capability 1. A column whose every value is
  identical carries NO information; it is a DEFAULT, not a link. And
  `capability_kind` is `eye/hand/thinking/voice` (perception and action), a
  DIFFERENT subject from code structure. So `capability_binding` is not the home.

  `onto_link` HAS the right shape and is why this table MIRRORS it. But its
  endpoints are `onto_concept` rows, and it carries no `unit` and no `cite_ref` —
  the two things that make a connection PROVABLE rather than asserted.

A ROUTE IS AN EDGE, NOT A NODE. The user asked whether a route is a factor under a
capability, an API, or a function. MEASURED: it is none of them.
  * `taxonomy_level_registry` declares eight NODE levels (channel, module,
    capability, api, function, db_table, db_field, service). A route is in NONE.
  * an API (`api_registry.method+path`) and a function (`function_registry`) are
    the route's TWO ENDS, not the route.
  * `terminology_registry.entity_edge` already defines the concept: "the edge that
    PLACES it in the graph ... an entity with no edge is not in the graph at all."

THE UNIT IS THE POINT. A `cite_ref` proves a connection EXISTS (the `where`
question). It does NOT prove the connection is HEALTHY. The four ways a
connection breaks each need their own unit, and each is a COUNT with a named
subject — the rule `factor_first_principle` states for a factor:
  * NOWHERE   one end does not resolve          (count of unresolved endpoints)
  * NEVER     nothing on the `from` side calls the `to` side  (count of call sites)
  * ONE_WAY   it calls but nothing calls back   (count of reverse call sites)
  * STALE     it resolves, but not to the current declaration (count of drifted refs)
  * OK        all of the above are zero
A route WITHOUT a unit cannot be judged, so it is REFUSED — the same rule that
made `UNKNOWN` the wrong answer earlier in this session.

`route` IS REGISTERED AS A TERM UNDER TWO PARENTS. MEASURED why one row cannot do
it: `route` already names an HTTP ENDPOINT (`route_inventory.py` measures 187 of
them; `subject_kind_registry.kind_key='route'` says "an HTTP route literal"), AND
this connection. `add_term`'s uniqueness is `(parent_term_id, term_key)`, and
`term_key` has no UNIQUE index, so ONE word with TWO rows is what the register is
FOR. Renaming would break every existing reader; two parented rows is the fix.
"""
from __future__ import annotations

import sqlite3
from typing import Any

# The two failure modes that are NOT "healthy", so a reader cannot mistake a
# zero-count for a pass. `OK` is the only state that is all-zero; the others name
# WHICH count is non-zero, because "the connection is broken" is not actionable.
HEALTH_OK = "OK"
HEALTH_NOWHERE = "NOWHERE"
HEALTH_NEVER = "NEVER"
HEALTH_ONE_WAY = "ONE_WAY"
HEALTH_STALE = "STALE"

# The states and the UNIT each is measured in. A MAPPING, not a branch chain: a
# new failure mode is a row. The unit NAMES ITS SUBJECT, because
# `factor_first_principle.assert_measurable` refuses a unit that does not.
HEALTH_UNITS: dict[str, str] = {
    HEALTH_OK: "count of unresolved endpoints AND missing call sites AND drifted refs",
    HEALTH_NOWHERE: "count of endpoints of this route that do not resolve",
    HEALTH_NEVER: "count of call sites from the `from` side to the `to` side",
    HEALTH_ONE_WAY: "count of reverse call sites from the `to` side to the `from` side",
    HEALTH_STALE: "count of endpoint refs that differ from the current declaration",
}

ROUTE_DDL = """
CREATE TABLE IF NOT EXISTS route_registry (
    route_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    route_key     TEXT    NOT NULL UNIQUE,
    -- THE TWO ENDS. Both must RESOLVE; a route with a dangling end is NOWHERE.
    from_kind     TEXT    NOT NULL,
    from_ref      TEXT    NOT NULL,
    to_kind       TEXT    NOT NULL,
    to_ref        TEXT    NOT NULL,
    -- THE RELATION, mirroring `onto_link.rel` (contains / uses_module / ...).
    rel           TEXT    NOT NULL DEFAULT 'calls',
    -- THE UNIT. A route with no unit cannot be judged, so this is NOT NULL.
    unit          TEXT    NOT NULL,
    -- THE PROOF. A command that MEASURES the unit, so the health is re-derivable
    -- rather than remembered. A route with no command is a claim, not a route.
    evidence_cmd  TEXT    NOT NULL,
    -- THE STATE, and the number behind it. `health_count` is what `health` is
    -- DERIVED from, so the two cannot disagree.
    health        TEXT    NOT NULL DEFAULT 'UNKNOWN'
                  CHECK (health IN ('OK','NOWHERE','NEVER','ONE_WAY','STALE','UNKNOWN')),
    health_count  INTEGER,
    -- THE CITED SOURCE, required and CHECKABLE (terminology_cite.verify_cite_ref).
    cite_ref      TEXT    NOT NULL,
    declared_by   TEXT    NOT NULL DEFAULT 'NA',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# The routes MEASURED this session. They are SEEDED, not left as an empty table:
# `capability_binding` and `fault_factor_trace` were built and never written, and
# a declared-but-empty register is the defect this work answers.
SEED_ROUTES: list[dict[str, Any]] = [
    {
        "route_key": "questions_to_steps",
        "from_kind": "function", "from_ref": "logic_generator.generate",
        "to_kind": "function", "to_ref": "question_flow.steps_from_questions",
        "rel": "calls",
        "unit": "count of generated questions that became a workflow_step row",
        "evidence_cmd": "python run_factory_line_once.py --subject table:code_registry",
        "cite_ref": "run_factory_line_once.py:161",
        "note": "MEASURED: 42 questions -> 42 steps + 1 verdict step",
    },
    {
        "route_key": "declared_rule_to_oracle",
        "from_kind": "table", "from_ref": "field_tdd_rule",
        "to_kind": "function", "to_ref": "llm_100_run_harness.oracle_for",
        "rel": "resolves",
        "unit": "count of rounds judged by the DECLARED check instead of the phone oracle",
        "evidence_cmd": "python _proof_rule_unit.py",
        "cite_ref": "llm_100_run_harness.py:392",
        "note": "MEASURED before: source=default_phone. After: source=field_rule (field_tdd_rule:16)",
    },
    {
        "route_key": "unit_to_judge_instruction",
        "from_kind": "function", "from_ref": "field_rule_declare.declared_instruction",
        "to_kind": "function", "to_ref": "llm_100_run_harness.judge_instruction_for",
        "rel": "derives",
        "unit": "count of option values judged correctly when the instruction is DERIVED",
        "evidence_cmd": "python _proof_rule_unit.py",
        "cite_ref": "field_rule_declare.py:338",
        "note": "MEASURED: 21/22 hand-written vs 22/22 derived, 4 repeats",
    },
    {
        "route_key": "step_position_to_question",
        "from_kind": "table", "from_ref": "workflow_step.step_no",
        "to_kind": "field", "to_ref": "logic_generator.generate[].question_id",
        "rel": "indexes",
        "unit": "count of step_no values whose question_id is the SAME position in the list",
        "evidence_cmd": "python run_factory_line_once.py --subject table:code_registry",
        "cite_ref": "run_factory_line_once.py:196",
        "note": "the link is POSITION, by construction in steps_from_questions",
    },
    # --- the watchdog / heartbeat connections (plan WATCHDOG.HEARTBEAT.SKILL) --
    #
    # WHY THESE THREE EXIST: MEASURED 2026-09-24, `route_registry` had 4 rows and
    # NONE mentioned the helper, the watchdog or the worker. So when the watchdog
    # died (last row 2026-09-14 11:46, 10 days silent) there was no declared
    # connection to QUERY, and the absence had to be found by reading timestamps
    # by hand. A declared route with a unit is what turns that into a query.
    {
        "route_key": "watchdog_watches_helper",
        "from_kind": "function", "from_ref": "helper_watchdog.probe_helper",
        "to_kind": "function", "to_ref": "mouse_spot_helper",
        "rel": "watches",
        "unit": "seconds since the newest watchdog_log row for this worker",
        "evidence_cmd": "python watchdog_health.py",
        "cite_ref": "watchdog_health.py:78",
        "note": "the SAME freshness unit detects the helper's death and the "
                "watchdog's own death — one unit, two consumers",
    },
    {
        "route_key": "worker_reports_heartbeat",
        "from_kind": "function", "from_ref": "worker_heartbeat_service.send_heartbeat",
        "to_kind": "table", "to_ref": "worker_heartbeat",
        "rel": "reports",
        "unit": "seconds since this worker's newest heartbeat row",
        "evidence_cmd": "python watchdog_health.py",
        "cite_ref": "watchdog_health.py:86",
        "note": "MEASURED: business_alive distinct=1 and pid distinct=1, so the "
                "health answer must come from the ROW'S AGE, not the column",
    },
    {
        "route_key": "helper_process_identity",
        "from_kind": "function", "from_ref": "watchdog_health.live_helper_pids",
        "to_kind": "function", "to_ref": "mouse_spot_helper",
        "rel": "counts",
        "unit": "count of live mouse_spot_helper.py processes",
        "evidence_cmd": "python watchdog_health.py",
        "cite_ref": "watchdog_health.py:93",
        "note": "MEASURED: 2 live helpers (pids 20812, 24080) with no watchdog "
                "running; >1 is an ALARM and kills nothing",
    },
]


class RouteError(ValueError):
    """A route cannot be declared as stated."""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or "agent.db"))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the route register. Idempotent."""
    conn.executescript(ROUTE_DDL)
    conn.commit()
    return {"ok": True, "table": "route_registry"}


def _assert_unit(unit: str, route_key: str) -> None:
    """REFUSE a unit that names no subject.

    The rule is `factor_first_principle.assert_measurable`'s: a unit that names no
    subject cannot be audited, and `count` / `value` / `total` name nothing.
    """
    text = str(unit or "").strip()
    if not text:
        raise RouteError("route %r: a route with no unit cannot be judged"
                         % route_key)
    lowered = text.lower()
    if lowered in ("count", "value", "total", "number", "thing", "x"):
        raise RouteError(
            "route %r: unit %r names NO SUBJECT, so a number in it cannot be "
            "audited" % (route_key, text))
    # A unit must say what it counts or measures, i.e. it carries a noun phrase.
    if lowered.startswith("count") and len(lowered.split()) < 3:
        raise RouteError(
            "route %r: unit %r says 'count' but not OF WHAT" % (route_key, text))


def _assert_cite(cite_ref: str, route_key: str) -> None:
    """REFUSE an uncheckable citation.

    MEASURED, and this defect recurred THREE times: `worker_identity_binding`'s
    six rows all carry `dimension_binding_registry.py:SUBJECT_KIND_BINDING_SEED`,
    which is a SYMBOL, not a `path:line`, and `citation_discipline.is_citation`
    returns False for it. A register that accepts such a ref cannot tell a
    citation from a sentence about one.
    """
    text = str(cite_ref or "").strip()
    if not text:
        raise RouteError("route %r: no citation, no route" % route_key)
    try:
        import terminology_cite as tc
        ok, msg = tc.verify_cite_ref(text)
    except Exception as exc:
        raise RouteError("route %r: cite check failed: %s" % (route_key, exc))
    if not ok:
        raise RouteError("route %r: cite_ref %r is not checkable: %s"
                         % (route_key, text, msg))


def declare_route(conn: sqlite3.Connection, route_key: str, *,
                  from_kind: str, from_ref: str,
                  to_kind: str, to_ref: str,
                  unit: str, evidence_cmd: str, cite_ref: str,
                  rel: str = "calls", declared_by: str = "NA",
                  commit: bool = True) -> dict[str, Any]:
    """Declare ONE route. Idempotent on `route_key`.

    REFUSES a route with no unit, a unit naming no subject, and an uncheckable
    citation. All three are REFUSALS rather than defaults, because a route that
    cannot be judged is exactly the connection failure this table exists to stop.
    """
    key = str(route_key or "").strip()
    if not key:
        raise RouteError("route_key is required")
    for label, val in (("from_kind", from_kind), ("from_ref", from_ref),
                       ("to_kind", to_kind), ("to_ref", to_ref)):
        if not str(val or "").strip():
            raise RouteError("route %r: %s is required (a route has TWO ends)"
                             % (key, label))
    _assert_unit(unit, key)
    _assert_cite(cite_ref, key)
    if not str(evidence_cmd or "").strip():
        raise RouteError(
            "route %r: no evidence_cmd, so the unit can never be re-measured — "
            "the health would be remembered rather than derived" % key)
    existing = conn.execute("SELECT route_id FROM route_registry WHERE route_key=?",
                            (key,)).fetchone()
    if existing:
        return {"ok": True, "action": "already_declared",
                "route_id": int(existing[0]), "route_key": key}
    cur = conn.execute(
        "INSERT INTO route_registry (route_key, from_kind, from_ref, to_kind, "
        "to_ref, rel, unit, evidence_cmd, cite_ref, declared_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, str(from_kind), str(from_ref), str(to_kind), str(to_ref),
         str(rel), str(unit), str(evidence_cmd), str(cite_ref), str(declared_by)))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "route_id": int(cur.lastrowid),
            "route_key": key}


def seed_routes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Declare the routes MEASURED this session. Idempotent.

    Returns `{ok, created, already, rows}`. `rows` is the count AFTER seeding, so
    a caller can assert the table is NOT empty — the defect the 24 empty tables
    (6 with `cite_ref`) represent.
    """
    created = already = 0
    for spec in SEED_ROUTES:
        s = dict(spec)
        s.pop("note", None)
        r = declare_route(conn, s.pop("route_key"), **s)
        if r["action"] == "created":
            created += 1
        else:
            already += 1
    return {"ok": True, "created": created, "already": already,
            "rows": conn.execute("SELECT COUNT(*) FROM route_registry").fetchone()[0]}


def health_unit(state: str) -> str:
    """The UNIT a health state is measured in. '' for an unknown state.

    '' for unknown, deliberately: a caller can then tell "no such state" from "a
    state with no unit", and it never guesses a unit for something it does not
    know — the same rule `evidence_source` follows.
    """
    return HEALTH_UNITS.get(str(state or "").strip().upper(), "")


def derive_health(*, unresolved_endpoints: int = 0,
                  forward_call_sites: int = 0,
                  reverse_call_sites: int = 0,
                  drifted_refs: int = 0) -> dict[str, Any]:
    """DERIVE the health from its counts. The counts are the measurement.

    Returns `{health, count, unit, measured}`. Priority is by TIER, like
    `failure_axis`: a dangling end (NOWHERE) dominates, because a route that does
    not resolve cannot be judged for anything else. The priority is ORDERED, not
    a chain of `if`s on names, so a new state is a row in `HEALTH_UNITS` plus one
    entry in the ordered list below.
    """
    checks = [
        (HEALTH_NOWHERE, int(unresolved_endpoints)),
        (HEALTH_NEVER, 0 if forward_call_sites else 1),
        (HEALTH_ONE_WAY, 0 if reverse_call_sites else 1),
        (HEALTH_STALE, int(drifted_refs)),
    ]
    measured = {"unresolved_endpoints": int(unresolved_endpoints),
                "forward_call_sites": int(forward_call_sites),
                "reverse_call_sites": int(reverse_call_sites),
                "drifted_refs": int(drifted_refs)}
    for state, count in checks:
        if count > 0:
            return {"health": state, "count": count, "unit": HEALTH_UNITS[state],
                    "measured": measured}
    return {"health": HEALTH_OK, "count": 0, "unit": HEALTH_UNITS[HEALTH_OK],
            "measured": measured}


def record_health(conn: sqlite3.Connection, route_key: str, *,
                  health: str, health_count: int,
                  commit: bool = True) -> dict[str, Any]:
    """Store a DERIVED health. REFUSES a state with no declared unit."""
    if not health_unit(health):
        return {"ok": False, "code": "UNKNOWN_STATE",
                "message": ("health %r has no declared unit, so its number "
                            "cannot be audited; known states: %s"
                            % (health, sorted(HEALTH_UNITS)))}
    row = conn.execute("SELECT route_id FROM route_registry WHERE route_key=?",
                       (str(route_key),)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_ROUTE",
                "message": "no route_registry row with route_key=%r" % route_key}
    conn.execute("UPDATE route_registry SET health=?, health_count=?, "
                 "updated_at=datetime('now') WHERE route_key=?",
                 (str(health).upper(), int(health_count), str(route_key)))
    if commit:
        conn.commit()
    return {"ok": True, "route_key": str(route_key),
            "health": str(health).upper(), "count": int(health_count),
            "unit": health_unit(health)}


def routes_of(conn: sqlite3.Connection, *, health: str | None = None
              ) -> list[dict[str, Any]]:
    """The declared routes, optionally filtered by health."""
    if health:
        rows = conn.execute("SELECT * FROM route_registry WHERE health=? "
                            "ORDER BY route_key", (str(health).upper(),))
    else:
        rows = conn.execute("SELECT * FROM route_registry ORDER BY route_key")
    return [dict(r) for r in rows]


def register_route_term(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register `route` as a TERM under TWO parents.

    THE USER: *"A and B can register by 2 row, this is why to have
    terminology-register"*. MEASURED support: `add_term` is idempotent on
    `(parent_term_id, term_key)` — `SELECT term_id FROM terminology_registry
    WHERE IFNULL(parent_term_id,-1)=IFNULL(?,-1) AND term_key=?` — and `term_key`
    has NO UNIQUE index. So two rows with one word is the SUPPORTED shape.

    The parents are themselves registered first, because a child needs a parent id
    and a term with no parent is a word with no taxonomy.
    """
    import terminology_registry as tr
    out: dict[str, Any] = {"parents": {}, "terms": {}}
    # The two SENSES get a parent each, so the word is not ambiguous.
    parents = [
        ("connection", "A declared link between two resolvable things: a from, a "
                       "to, and a relation. A connection is an EDGE, not a node — "
                       "it is not one of the eight taxonomy LEVELS. Its health is "
                       "measured, not asserted.",
         "purpose_route_registry.py:1"),
        ("http_endpoint", "An HTTP surface: a method and a path. It is a NODE "
                          "(taxonomy level `api`), and it is ONE END of a "
                          "connection, not a connection itself.",
         "route_inventory.py:1"),
    ]
    for k, d, c in parents:
        r = tr.add_term(conn, k, definition=d, cite_ref=c, term_kind="entity",
                        commit=False)
        out["parents"][k] = r.get("code") or r.get("term_id")
    pid_conn = conn.execute(
        "SELECT term_id FROM terminology_registry WHERE "
        "IFNULL(parent_term_id,-1)=-1 AND term_key='connection'").fetchone()
    pid_http = conn.execute(
        "SELECT term_id FROM terminology_registry WHERE "
        "IFNULL(parent_term_id,-1)=-1 AND term_key='http_endpoint'").fetchone()
    if pid_conn:
        r = tr.add_term(
            conn, "route",
            definition=("A CONNECTION: a declared link from one thing to another, "
                        "with a relation, a unit, and a cited proof. `purpose_route_"
                        "register` is the pattern — purpose -> service -> workflow. "
                        "This sense is DISTINCT from `route` as an HTTP endpoint."),
            cite_ref="purpose_route_registry.py:28", term_kind="part",
            parent_term_id=int(pid_conn[0]), commit=False)
        out["terms"]["route@connection"] = r.get("code") or r.get("term_id")
    if pid_http:
        r = tr.add_term(
            conn, "route",
            definition=("An HTTP ENDPOINT — method + path — as measured by "
                        "`route_inventory.py` (187 routes). It is a NODE at "
                        "taxonomy level `api`, and one END of a connection."),
            cite_ref="route_inventory.py:27", term_kind="part",
            parent_term_id=int(pid_http[0]), commit=False)
        out["terms"]["route@http_endpoint"] = r.get("code") or r.get("term_id")
    conn.commit()
    return out
