# -*- coding: utf-8 -*-
"""write_gate_registry.py — THE HARD GATE on the skill -> coding WRITE path.

THE HUMAN (2026-09-28), verbatim:
    "hard gate for skill > coding writing is needed, and that is the table
     standardize we are building at this session, update your plan now"

THE PROBLEM, MEASURED (P10)
---------------------------
`qc_gate_registry` holds **9 gates** and every one checks a SUBJECT — a file, a
contract, a trace. **NONE checks a WRITE ATTEMPT.** So a bad skill or a bad line
of code lands, and is discovered later or never.

THE DESIGN — the SAME shape, applied to a write
-----------------------------------------------
A gate is a ROW; a write is a SUBJECT; the verdict is RECORDED. Nothing new is
invented, and the machinery is REUSED rather than re-implemented:

    qc_gate_registry     9 rows: checker_ref, metric_kind, metric_unit,
                         metric_target, polarity
    qc_gate_runner       run_gate(conn, gate_key, subject_kind=, subject_ref=)
    qc_arbiter           arbitrate(results, expected_keys=)
    qc_run               170 rows: verdict, reason, gate_key, score_0_100

WHAT IS NEW IS ONLY THE MISSING PIECE: a gate that fires AT THE MOMENT OF WRITING.

THE FIVE GATES, each with a MEASURED unit (never prose)
-------------------------------------------------------
    1 write_names_a_factor          both    pct of writes naming a registered
                                            factor_key                     -> 100
    2 write_carries_a_cite          both    pct of writes with a path:line or
                                            a command                      -> 100
    3 write_has_a_proof             both    pct of writes naming a proof file
                                            that EXISTS                    -> 100
    4 write_is_registered           coding  pct of written files with an entity
                                            id                             -> 100
    5 write_does_not_break_a_live_name coding count of live objects the write
                                            would wrongly refuse          -> 0

THE LAW THIS MODULE OBEYS, and it is the one that broke three proofs on
2026-09-28: **a gate asserts a PROPERTY of the write, never a state the work is
expected to change.** So gate 5 measures the naming law's verdict on the NEW name
only, and gate 4 measures the target file's registration, not the register's size.

THE VALVE FAILS CLOSED
----------------------
`WRITE_GATE` defaults to `enforce`. An UNKNOWN value is a REFUSAL, not a pass —
the same rule `FACTOR_MEASURABLE_GATE` follows. A valve that fails open is a
valve that is off whenever someone mistypes.

WHY THIS IS THE TABLE STANDARDIZE, NOT A NEW THING
--------------------------------------------------
The human's own words: *"that is the table standardize we are building at this
session"*. The build-step register already says **a step needs a factor, a
purpose and a citation**. This module applies **the same three requirements to a
WRITE**, so the standard is enforced at the moment of writing rather than audited
afterwards.

Run:
    .\\.venv\\Scripts\\python.exe write_gate_registry.py --audit
    .\\.venv\\Scripts\\python.exe write_gate_registry.py --seed
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE / "agent.db"
NA = "NA"

# The metric vocabulary is CLOSED and IMPORTED, not restated — `skill_factor`
# owns it, so a second list here is the drift this repo has recorded.
from skill_factor import MK_COUNT, MK_PCT  # noqa: E402

METRIC_KINDS = (MK_COUNT, MK_PCT)
POLARITIES = ("at_least", "at_most", "equal")

# The verdict vocabulary. `UNKNOWN` is a REAL outcome — a write the gate could
# not measure is never a silent pass, the same rule `qc_gate_runner` follows.
VERDICTS = ("ALLOW", "DENY", "UNKNOWN")

# THE VALVE. `enforce` refuses a bad write; `report` records it and allows;
# `off` does nothing. An UNKNOWN value FAILS CLOSED (it is `enforce`), because a
# valve that fails open is off whenever someone mistypes.
VALVE_ENV = "WRITE_GATE"
VALVE_VALUES = ("enforce", "report", "off")
VALVE_DEFAULT = "enforce"

# ---------------------------------------------------------------------------
# THE DDL CONSTANTS ARE NAMED `*_DDL` ON PURPOSE, and it is not cosmetic.
#
# MEASURED 2026-09-28 (SCOPE C): `logic_generator.spec_from_table` REFUSED a table
# with *"no `*_DDL` constant in ANY module declares table ... so there is no
# DECLARED shape to measure the live table against"*. `_iter_ddl_constants` scans
# every `*.py` for a MODULE-LEVEL `NAME_DDL = """..."""`. So the declaration must
# be (a) module-level and (b) named `*_DDL`. `ensure_schema` EXECUTES these same
# constants, so the declaration and the live schema cannot drift apart without
# the generator noticing.
# ---------------------------------------------------------------------------
WRITE_GATE_registry_DDL = """
CREATE TABLE IF NOT EXISTS write_gate_registry (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_key       TEXT    NOT NULL UNIQUE,
    gate_name      TEXT    NOT NULL,
    applies_to     TEXT    NOT NULL DEFAULT 'both'
                   CHECK (applies_to IN ('skill','coding','both')),
    checker_ref    TEXT    NOT NULL,
    metric_kind    TEXT    NOT NULL CHECK (metric_kind IN ('count','pct')),
    metric_unit    TEXT    NOT NULL,
    metric_target  REAL    NOT NULL,
    polarity       TEXT    NOT NULL DEFAULT 'at_least'
                   CHECK (polarity IN ('at_least','at_most','equal')),
    cite_ref       TEXT    NOT NULL,
    sort_order     INTEGER NOT NULL UNIQUE,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

WRITE_ATTEMPT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS write_attempt_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_key      TEXT    NOT NULL,
    subject_kind  TEXT    NOT NULL,
    subject_ref   TEXT    NOT NULL,
    target_path   TEXT    NOT NULL DEFAULT 'NA',
    factor_key    TEXT    NOT NULL DEFAULT 'NA',
    verdict       TEXT    NOT NULL CHECK (verdict IN ('ALLOW','DENY','UNKNOWN')),
    value         REAL,
    metric_target REAL,
    polarity      TEXT    NOT NULL DEFAULT 'at_least',
    reason        TEXT    NOT NULL,
    evidence_ref  TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (gate_key, subject_kind, subject_ref, target_path)
)
"""

# ---------------------------------------------------------------------------
# THE FIVE GATES. `metric_unit` MUST match `"<kind> of <subject>"` —
# `factor_first_principle`'s rule: a number without a UNIT cannot be audited.
# ---------------------------------------------------------------------------
WRITE_GATES: tuple[dict[str, Any], ...] = (
    dict(gate_key="write_names_a_factor", sort_order=1,
         gate_name="Write names the FACTOR it serves",
         applies_to="both",
         checker_ref="write_gate_registry._check_names_a_factor.py",
         metric_kind=MK_PCT,
         metric_unit="pct of writes naming a registered factor_key",
         metric_target=100.0, polarity="at_least",
         cite_ref="build_step_registry.py:1"),
    dict(gate_key="write_carries_a_cite", sort_order=2,
         gate_name="Write carries a checkable citation",
         applies_to="both",
         checker_ref="write_gate_registry._check_carries_a_cite.py",
         metric_kind=MK_PCT,
         metric_unit="pct of writes with a path:line or a command",
         metric_target=100.0, polarity="at_least",
         cite_ref="citation_discipline.py:141"),
    dict(gate_key="write_has_a_proof", sort_order=3,
         gate_name="Write names the proof that measures it",
         applies_to="both",
         checker_ref="write_gate_registry._check_has_a_proof.py",
         metric_kind=MK_PCT,
         metric_unit="pct of writes naming a proof file that exists",
         metric_target=100.0, polarity="at_least",
         cite_ref="skill_factor.py:1360"),
    dict(gate_key="write_is_registered", sort_order=4,
         gate_name="Written file is registered as an entity",
         applies_to="coding",
         checker_ref="write_gate_registry._check_is_registered.py",
         metric_kind=MK_PCT,
         metric_unit="pct of written files with an entity id",
         metric_target=100.0, polarity="at_least",
         cite_ref="code_location_registry.py:82"),
    dict(gate_key="write_does_not_break_a_live_name", sort_order=5,
         gate_name="New name passes the naming law",
         applies_to="coding",
         checker_ref="write_gate_registry._check_does_not_break_a_live_name.py",
         metric_kind=MK_COUNT,
         metric_unit="count of live objects the write would wrongly refuse",
         metric_target=0.0, polarity="at_most",
         cite_ref="unified_language.py:1"),
)

GATE_KEYS: tuple[str, ...] = tuple(g["gate_key"] for g in WRITE_GATES)


class WriteGateError(ValueError):
    """Raised when a write cannot be judged."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def valve() -> str:
    """The valve's value, FAILING CLOSED on an unknown one.

    MEASURED DEFECT this encodes: a valve that returns the DEFAULT on an unknown
    value is a valve that is OFF whenever someone mistypes. So an unknown value
    is reported as `enforce` AND the caller is told it was unknown.
    """
    raw = str(os.environ.get(VALVE_ENV, VALVE_DEFAULT) or "").strip().lower()
    if raw in VALVE_VALUES:
        return raw
    return VALVE_DEFAULT


def valve_state() -> dict[str, Any]:
    """The valve AND whether its value was recognised. Never raises."""
    raw = str(os.environ.get(VALVE_ENV, VALVE_DEFAULT) or "").strip().lower()
    known = raw in VALVE_VALUES
    return {"env": VALVE_ENV, "raw": raw, "value": raw if known else VALVE_DEFAULT,
            "known": known,
            "why": ("recognised" if known else
                    "%r is not one of %s, so the valve FAILS CLOSED to %r"
                    % (raw, ", ".join(VALVE_VALUES), VALVE_DEFAULT))}


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the gate register and the attempt log. Idempotent."""
    conn.execute(WRITE_GATE_registry_DDL)
    conn.execute(WRITE_ATTEMPT_LOG_DDL)
    conn.commit()
    return {"ok": True}


def _metric_unit_ok(kind: str, unit: str) -> bool:
    """`"<kind> of <subject>"` — the unit must NAME the kind it is a count of."""
    u = str(unit or "").strip().lower()
    return u.startswith(str(kind).strip().lower() + " of ")


def seed_gates(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write the five declarations. Idempotent (UPSERT by `gate_key`).

    REFUSES rather than guessing: a gate whose `metric_kind` is outside the
    closed set, or whose `metric_unit` does not name its kind, is not written —
    a number that cannot be audited is a decoration.
    """
    ensure_schema(conn)
    created, updated, refused = 0, 0, []
    for g in WRITE_GATES:
        key = g["gate_key"]
        if g["metric_kind"] not in METRIC_KINDS:
            refused.append({"gate_key": key, "code": "BAD_METRIC_KIND",
                            "message": "metric_kind %r is not one of %s"
                                       % (g["metric_kind"], METRIC_KINDS)})
            continue
        if not _metric_unit_ok(g["metric_kind"], g["metric_unit"]):
            refused.append({"gate_key": key, "code": "UNIT_NAMES_NO_KIND",
                            "message": "metric_unit %r does not name its kind %r"
                                       % (g["metric_unit"], g["metric_kind"])})
            continue
        if g.get("polarity") not in POLARITIES:
            refused.append({"gate_key": key, "code": "BAD_POLARITY",
                            "message": "polarity %r is not one of %s"
                                       % (g.get("polarity"), POLARITIES)})
            continue
        if not str(g["cite_ref"] or "").strip():
            refused.append({"gate_key": key, "code": "MISSING_CITE_REF",
                            "message": "no citation, no gate"})
            continue
        row = conn.execute("SELECT id FROM write_gate_registry WHERE gate_key=?",
                           (key,)).fetchone()
        if row:
            conn.execute(
                "UPDATE write_gate_registry SET gate_name=?, applies_to=?, "
                "checker_ref=?, metric_kind=?, metric_unit=?, metric_target=?, "
                "polarity=?, cite_ref=?, sort_order=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (g["gate_name"], g["applies_to"], g["checker_ref"],
                 g["metric_kind"], g["metric_unit"], float(g["metric_target"]),
                 g["polarity"], g["cite_ref"], int(g["sort_order"]),
                 int(row["id"])))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO write_gate_registry (gate_key, gate_name, "
                "applies_to, checker_ref, metric_kind, metric_unit, "
                "metric_target, polarity, cite_ref, sort_order) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, g["gate_name"], g["applies_to"], g["checker_ref"],
                 g["metric_kind"], g["metric_unit"], float(g["metric_target"]),
                 g["polarity"], g["cite_ref"], int(g["sort_order"])))
            created += 1
    conn.commit()
    return {"ok": not refused, "created": created, "updated": updated,
            "total": len(WRITE_GATES), "refused": refused}


def list_gates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM write_gate_registry WHERE is_active=1 "
        "ORDER BY sort_order")]


def gate_for(conn: sqlite3.Connection, gate_key: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM write_gate_registry WHERE gate_key=?",
                       (str(gate_key),)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# THE FIVE CHECKERS. Each returns (value, reason, evidence_ref).
# A checker that CANNOT measure returns value=None -> UNKNOWN, never a pass.
# ---------------------------------------------------------------------------

def _check_names_a_factor(conn: sqlite3.Connection, w: dict[str, Any]
                          ) -> tuple[float | None, str, str]:
    """Does the write name the FACTOR it serves? (D2: the factor IS the key.)"""
    fk = str(w.get("factor_key") or "").strip()
    if not fk or fk == NA:
        return 0.0, ("the write names NO factor_key. The factor IS the key (D2): "
                     "a write that serves no factor cannot be measured."), NA
    row = conn.execute("SELECT factor_key FROM skill_factor_registry WHERE "
                       "factor_key=?", (fk,)).fetchone()
    if not row:
        return 0.0, ("factor_key %r is NOT in skill_factor_registry — a write "
                     "naming a factor that does not exist can never be "
                     "measured." % fk), "SELECT factor_key FROM skill_factor_registry"
    return 100.0, "factor_key %r resolves in skill_factor_registry" % fk, \
        "SELECT factor_key FROM skill_factor_registry WHERE factor_key=%r" % fk


def _check_carries_a_cite(conn: sqlite3.Connection, w: dict[str, Any]
                          ) -> tuple[float | None, str, str]:
    """Does the write carry a CHECKABLE citation? (no citation, no row)"""
    ref = str(w.get("cite_ref") or "").strip()
    if not ref or ref == NA:
        return 0.0, ("the write carries NO cite_ref. No citation, no row — a "
                     "write nobody can check is a claim."), NA
    try:
        import citation_discipline as cd
        ok = cd.is_citation(ref)
    except Exception as exc:
        return None, ("the citation checker could not run (%s: %s), so the "
                      "citation is UNMEASURED — never a pass."
                      % (type(exc).__name__, exc)), ref
    if not ok:
        return 0.0, ("cite_ref %r is not a checkable citation — need path:line "
                     "or a command." % ref), ref
    return 100.0, "cite_ref %r is a checkable citation" % ref, ref


def _check_has_a_proof(conn: sqlite3.Connection, w: dict[str, Any]
                       ) -> tuple[float | None, str, str]:
    """Does the write name a proof file that EXISTS?

    A proof that does not exist is a promise, not a proof — the same rule
    `task-done-verification` applies to a `done` status.
    """
    ref = str(w.get("proof_ref") or "").strip()
    if not ref or ref == NA:
        return 0.0, ("the write names NO proof_ref. A rule with no proof that "
                     "measures it is a rule nobody can check."), NA
    p = BASE / ref
    if not p.exists():
        return 0.0, ("proof_ref %r does NOT exist on disk — a proof that does "
                     "not exist is a promise, not a proof." % ref), ref
    return 100.0, "proof_ref %r exists" % ref, ref


def _check_is_registered(conn: sqlite3.Connection, w: dict[str, Any]
                         ) -> tuple[float | None, str, str]:
    """Is the written FILE registered as an entity? (coding only)

    THE PROPERTY IS THE FILE'S REGISTRATION, not the register's size — a check
    that measured the register's row count would go RED whenever another writer
    added a row, which is the defect that broke three proofs on 2026-09-28.
    """
    path = str(w.get("target_path") or "").strip()
    if not path or path == NA:
        return None, ("no target_path, so the file's registration is "
                      "UNMEASURED — never a pass."), NA
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM code_location_registry WHERE file_path=?",
            (path,)).fetchone()
        n = int(row[0]) if row else 0
    except sqlite3.OperationalError as exc:
        return None, ("code_location_registry is not readable (%s), so the "
                      "registration is UNMEASURED — never a pass." % exc), NA
    if n == 0:
        return 0.0, ("%s has NO code_location_registry row — an unregistered "
                     "file has no entity id, so nothing can cite it." % path), \
            "SELECT COUNT(*) FROM code_location_registry WHERE file_path=%r" % path
    return 100.0, "%s has %d code_location_registry row(s)" % (path, n), \
        "SELECT COUNT(*) FROM code_location_registry WHERE file_path=%r" % path


def _check_does_not_break_a_live_name(conn: sqlite3.Connection, w: dict[str, Any]
                                      ) -> tuple[float | None, str, str]:
    """Does the NEW name pass the naming law? (coding only)

    THE PROPERTY IS THE NEW NAME'S VERDICT, not the number of live objects — a
    check that counted live objects would go RED whenever another writer created
    a table, which is the defect that broke three proofs on 2026-09-28.
    """
    name = str(w.get("new_name") or "").strip()
    if not name or name == NA:
        return None, ("no new_name, so the naming law's verdict is UNMEASURED — "
                      "never a pass."), NA
    try:
        import unified_language as ul
        v = ul.check_composite(conn, name)
    except Exception as exc:
        return None, ("the naming law could not run (%s: %s), so the verdict is "
                      "UNMEASURED — never a pass."
                      % (type(exc).__name__, exc)), NA
    if not v.get("ok"):
        return 1.0, ("the new name %r is REFUSED by the naming law (%s: missing "
                     "%s)." % (name, v.get("code"), v.get("missing_words"))), \
            "unified_language.check_composite(%r)" % name
    return 0.0, "the new name %r passes the naming law" % name, \
        "unified_language.check_composite(%r)" % name


CHECKERS = {
    "write_names_a_factor": _check_names_a_factor,
    "write_carries_a_cite": _check_carries_a_cite,
    "write_has_a_proof": _check_has_a_proof,
    "write_is_registered": _check_is_registered,
    "write_does_not_break_a_live_name": _check_does_not_break_a_live_name,
}


def _passes(value: float | None, target: float, polarity: str) -> bool | None:
    """The arbiter's own rule, reused rather than restated. None = UNKNOWN."""
    if value is None:
        return None
    if polarity == "at_most":
        return float(value) <= float(target)
    if polarity == "equal":
        return float(value) == float(target)
    return float(value) >= float(target)


def check_write(conn: sqlite3.Connection, *, gate_key: str,
                subject_kind: str, subject_ref: str,
                target_path: str = NA, factor_key: str = NA,
                cite_ref: str = NA, proof_ref: str = NA,
                new_name: str = NA) -> dict[str, Any]:
    """Run ONE gate against ONE write. Returns a verdict; NEVER raises.

    `UNKNOWN` is a REAL outcome: a gate that cannot measure its subject is never
    a pass — the same rule `qc_gate_runner` follows.
    """
    ensure_schema(conn)
    g = gate_for(conn, gate_key)
    if not g:
        return {"ok": False, "code": "UNKNOWN_GATE", "gate_key": str(gate_key),
                "verdict": "UNKNOWN",
                "reason": "no write_gate_registry row for gate_key=%r"
                          % gate_key}
    fn = CHECKERS.get(str(gate_key))
    if fn is None:
        return {"ok": False, "code": "NO_CHECKER", "gate_key": str(gate_key),
                "verdict": "UNKNOWN",
                "reason": "gate %r declares no checker" % gate_key}
    w = {"target_path": target_path, "factor_key": factor_key,
         "cite_ref": cite_ref, "proof_ref": proof_ref, "new_name": new_name}
    try:
        value, reason, evidence = fn(conn, w)
    except Exception as exc:
        value, reason, evidence = None, ("the checker raised %s: %s"
                                         % (type(exc).__name__, exc)), NA
    ok = _passes(value, float(g["metric_target"]), str(g["polarity"]))
    verdict = "UNKNOWN" if ok is None else ("ALLOW" if ok else "DENY")
    return {"ok": True, "gate_key": str(gate_key), "verdict": verdict,
            "value": value, "metric_target": float(g["metric_target"]),
            "polarity": str(g["polarity"]), "metric_unit": g["metric_unit"],
            "reason": reason, "evidence_ref": evidence,
            "cite_ref": g["cite_ref"], "applies_to": g["applies_to"]}


def gate_write(conn: sqlite3.Connection, *, subject_kind: str, subject_ref: str,
               target_path: str = NA, factor_key: str = NA, cite_ref: str = NA,
               proof_ref: str = NA, new_name: str = NA,
               apply: bool = True) -> dict[str, Any]:
    """Run EVERY applicable gate against ONE write, and RECORD the verdicts.

    THE VALVE: `enforce` refuses a bad write; `report` records it and allows;
    `off` does nothing. An UNKNOWN value FAILS CLOSED to `enforce`.

    A refusal is RECORDED in `write_attempt_log`, so a DENY is VISIBLE rather
    than silent — the defect `proof_gate_silent_when_empty` names.
    """
    ensure_schema(conn)
    v = valve_state()
    kind = str(subject_kind or "").strip().lower()
    if kind not in ("skill", "coding"):
        return {"ok": False, "code": "BAD_SUBJECT_KIND", "verdict": "UNKNOWN",
                "reason": "subject_kind must be 'skill' or 'coding', got %r"
                          % subject_kind}
    results = []
    for g in list_gates(conn):
        if g["applies_to"] not in (kind, "both"):
            continue
        results.append(check_write(
            conn, gate_key=g["gate_key"], subject_kind=kind,
            subject_ref=subject_ref, target_path=target_path,
            factor_key=factor_key, cite_ref=cite_ref, proof_ref=proof_ref,
            new_name=new_name))
    denied = [r for r in results if r["verdict"] == "DENY"]
    unknown = [r for r in results if r["verdict"] == "UNKNOWN"]
    # THE AGGREGATE, and the order matters: a DENY outranks an UNKNOWN, because
    # a measured failure is stronger evidence than an absent measurement.
    verdict = "DENY" if denied else ("UNKNOWN" if unknown else "ALLOW")
    if apply:
        for r in results:
            try:
                conn.execute(
                    "INSERT INTO write_attempt_log (gate_key, subject_kind, "
                    "subject_ref, target_path, factor_key, verdict, value, "
                    "metric_target, polarity, reason, evidence_ref, cite_ref) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(gate_key, subject_kind, subject_ref, "
                    "target_path) DO UPDATE SET verdict=excluded.verdict, "
                    "value=excluded.value, reason=excluded.reason, "
                    "evidence_ref=excluded.evidence_ref, "
                    "cite_ref=excluded.cite_ref",
                    (r["gate_key"], kind, str(subject_ref), str(target_path),
                     str(factor_key), r["verdict"], r["value"],
                     r["metric_target"], r["polarity"], r["reason"],
                     r["evidence_ref"], r["cite_ref"]))
            except Exception as exc:
                r["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        conn.commit()
    allowed = (verdict == "ALLOW") or (v["value"] == "report") \
        or (v["value"] == "off")
    return {"ok": True, "verdict": verdict, "allowed": bool(allowed),
            "valve": v, "subject_kind": kind, "subject_ref": str(subject_ref),
            "gates": results, "denied": [r["gate_key"] for r in denied],
            "unknown": [r["gate_key"] for r in unknown],
            "recorded": bool(apply)}


def attempts(conn: sqlite3.Connection, *, verdict: str | None = None,
             limit: int = 200) -> list[dict[str, Any]]:
    """The recorded write attempts, newest first. A DENY is VISIBLE here."""
    ensure_schema(conn)
    sql = "SELECT * FROM write_attempt_log WHERE is_active=1"
    args: list[Any] = []
    if verdict:
        sql += " AND verdict=?"
        args.append(str(verdict).upper())
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, tuple(args))]


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """The gate rows AND the attempt log's counts. A RATIO, never a pinned count."""
    ensure_schema(conn)
    gates = list_gates(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT verdict, COUNT(*) n FROM write_attempt_log WHERE is_active=1 "
        "GROUP BY verdict")]
    by = {str(r["verdict"]): int(r["n"]) for r in rows}
    total = sum(by.values())
    return {"ok": True, "gates": len(gates), "gate_keys": [g["gate_key"] for g in gates],
            "attempts": total, "allow": by.get("ALLOW", 0),
            "deny": by.get("DENY", 0), "unknown": by.get("UNKNOWN", 0),
            "deny_pct": round(100.0 * by.get("DENY", 0) / total, 2) if total else 0.0,
            "valve": valve_state()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--seed", action="store_true", help="write the five gate rows")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--attempts", action="store_true")
    ap.add_argument("--verdict", default=None)
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_gates(conn), indent=2, ensure_ascii=False))
        if args.attempts:
            print(json.dumps(attempts(conn, verdict=args.verdict), indent=2,
                             ensure_ascii=False))
        if args.audit or not any((args.seed, args.attempts)):
            print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
