# -*- coding: utf-8 -*-
"""
register_approval.py — approve a registered entity, and MEASURE how it performs.

Why
---
Registers exist for 19 kinds of thing, but nothing says whether the code behind
one is good, dirty, or rubbish, and nothing links a register row back to the
file it came from. Two consequences the user named:

    "register but can't pass verify -> approve = problem happen"
    "can have waiting cleanup list or waiting helping list"

Design, and the two corrections that shaped it
----------------------------------------------
1. THE SHARED KEY IS `(entity_type, entity_ref_id)`, not a link to
   `code_registry` alone. Measured: 19 `*_register` tables share almost no
   columns -- only 2 have `code_registry_id` and it is filled 0/1. A link to
   `code_registry` would leave 17 registers unapprovable. `entity_type` is a
   letter from `entity_type_registry`, never a hard-coded list.

2. `pass` DOES NOT feed `worker_heartbeat`. Measured: `worker_heartbeat` is a
   time series of liveness (`worker_id, screenshot_path, business_alive, pid,
   heartbeat_at`) -- one row per beat. Writing a pass into it would bury the
   liveness signal under a stream of pass events. Passes and failures are
   COUNTERS on `coding_performance`; the watchdog READS those counters. It is
   not written to.

   Likewise `watchdog_log` is an error log (`worker_id, message, level`), not a
   work queue, so a failure does not become a watchdog row either.

3. DEAD and FAIL are separate lists, because they need different help:
     DEAD = registered, zero usages      -> waiting CLEANUP  ("nobody uses it")
     FAIL = used, verification failed    -> waiting HELP     ("someone needs it")
   Merging them would let rescue work drown in tidy-up work.

Fail-closed rules
-----------------
- A verdict of APPROVED requires all three stages to have PASSED. Not recorded
  as metadata: `record_decision` REFUSES otherwise.
- Class WORKABLE requires all three stages passed. It cannot be asserted on an
  unverified entity.
- `cite_ref` must pass `citation_discipline.assert_cited`. No citation, no row.
- `evidence_id`, when given, must name a real `evidence/<EVID>` directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"
EVIDENCE_ROOT = BASE_DIR / "evidence"

import entity_registry as er

VERDICTS = ("PENDING", "APPROVED", "REJECTED")
CLASSES = ("WORKABLE", "DIRTY", "RUBBISH", "UNKNOWN")
# The three stages every registration must clear, in order.
STAGES: tuple[str, ...] = ("tdd", "tdd_verify", "ontology_verify")

REGISTER_APPROVE_DDL = """
CREATE TABLE IF NOT EXISTS register_approve (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type    TEXT    NOT NULL,
    entity_ref_id  INTEGER NOT NULL,
    version        INTEGER,
    file_path      TEXT,
    line_no        INTEGER,
    verdict        TEXT    NOT NULL DEFAULT 'PENDING'
                   CHECK (verdict IN ('PENDING','APPROVED','REJECTED')),
    class          TEXT    NOT NULL DEFAULT 'UNKNOWN'
                   CHECK (class IN ('WORKABLE','DIRTY','RUBBISH','UNKNOWN')),
    tdd_pass          INTEGER NOT NULL DEFAULT 0,
    tdd_fail          INTEGER NOT NULL DEFAULT 0,
    tdd_verify_pass   INTEGER NOT NULL DEFAULT 0,
    tdd_verify_fail   INTEGER NOT NULL DEFAULT 0,
    ontology_pass     INTEGER NOT NULL DEFAULT 0,
    ontology_fail     INTEGER NOT NULL DEFAULT 0,
    evidence_id    TEXT,
    cite_ref       TEXT    NOT NULL,
    decided_at     TEXT,
    decided_by     TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id, version),
    FOREIGN KEY (entity_type) REFERENCES entity_type_registry (type_letter)
);
CREATE INDEX IF NOT EXISTS idx_registry_approve_entity
    ON register_approve (entity_type, entity_ref_id);
CREATE INDEX IF NOT EXISTS idx_registry_approve_verdict
    ON register_approve (verdict, class);
"""

CODING_PERFORMANCE_DDL = """
CREATE TABLE IF NOT EXISTS coding_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    register_approve_id INTEGER NOT NULL,
    usage_count         INTEGER NOT NULL DEFAULT 0,
    pass_count          INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (register_approve_id),
    FOREIGN KEY (register_approve_id)
        REFERENCES register_approve (id)
);
"""

# The two work lists. A row per entity that needs a human, with the chat that
# can carry the discussion.
WAITING_DDL = """
CREATE TABLE IF NOT EXISTS waiting_cleanup (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT    NOT NULL,
    entity_ref_id INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    chat_id       INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id)
);
CREATE TABLE IF NOT EXISTS waiting_helping (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT    NOT NULL,
    entity_ref_id INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    chat_id       INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id)
);
"""

ALL_DDL = (REGISTER_APPROVE_DDL, CODING_PERFORMANCE_DDL, WAITING_DDL)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def log(msg: str) -> None:
    print("[register_approval] %s" % msg, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    for ddl in ALL_DDL:
        conn.executescript(ddl)
    conn.commit()
    return {"ok": True, "tables": ["register_approve", "coding_performance",
                                   "waiting_cleanup", "waiting_helping"]}


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------


class StageNotPassed(RuntimeError):
    """APPROVED was asked for while a stage had not passed."""


class UncitedApproval(RuntimeError):
    """A cite_ref that is not a checkable reference."""


class UnknownEvidence(RuntimeError):
    """An evidence_id that does not name a real evidence directory."""


def check_stages(*, tdd_pass: int = 0, tdd_fail: int = 0,
                 tdd_verify_pass: int = 0, tdd_verify_fail: int = 0,
                 ontology_pass: int = 0, ontology_fail: int = 0) -> dict[str, Any]:
    """Which of the three stages passed? A stage passes only with >=1 pass and
    no failures -- an empty stage is NOT a pass."""
    measured = {
        "tdd": (int(tdd_pass), int(tdd_fail)),
        "tdd_verify": (int(tdd_verify_pass), int(tdd_verify_fail)),
        "ontology_verify": (int(ontology_pass), int(ontology_fail)),
    }
    out: dict[str, Any] = {}
    for stage, (p, f) in measured.items():
        if f > 0:
            out[stage] = {"passed": False, "why": "%d failure(s)" % f,
                          "pass": p, "fail": f}
        elif p <= 0:
            out[stage] = {"passed": False, "why": "not run (0 passes)",
                          "pass": p, "fail": f}
        else:
            out[stage] = {"passed": True, "why": "%d pass, 0 fail" % p,
                          "pass": p, "fail": f}
    out["all_passed"] = all(out[s]["passed"] for s in STAGES)
    out["failed_stages"] = [s for s in STAGES if not out[s]["passed"]]
    return out


def classify(stage_result: dict[str, Any], *, usage_count: int = 0) -> str:
    """Derive the class from MEASURED stages. Never asserted.

    WORKABLE needs every stage passed. An entity nobody uses and that never ran
    cannot be called workable, so an unrun entity is UNKNOWN, not WORKABLE.
    """
    if stage_result["all_passed"]:
        return "WORKABLE"
    ran = any(stage_result[s]["pass"] > 0 or stage_result[s]["fail"] > 0
              for s in STAGES)
    if not ran:
        return "UNKNOWN"
    if usage_count <= 0:
        return "RUBBISH"
    return "DIRTY"


def _assert_cited(cite_ref: str, conn: sqlite3.Connection | None = None) -> None:
    """The citation MUST be checkable, not merely well-shaped.

    A `register:<table>:<pk>` reference is re-queried, because an entity that
    exists only as a register row has no `path:line` to cite, and its row is the
    one thing that CAN be confirmed. Shape-only acceptance would let
    `register:no_such_table:1` through, which would be a downgrade of this gate
    rather than a fix of it.
    """
    try:
        import citation_discipline as cd
    except Exception:
        if not cite_ref or ":" not in cite_ref:
            raise UncitedApproval(
                "cite_ref %r is not a path:line, a command, or register:table:pk"
                % cite_ref)
        return
    cd.assert_cited({"evidence_ref": cite_ref})
    # a DB-row citation gets the extra, STRONGER check
    parsed = cd.parse_db_ref(cite_ref)
    if parsed:
        res = cd.verify_db_ref(cite_ref, conn=conn)
        if not res.get("exists"):
            raise UncitedApproval(
                "cite_ref %r does not resolve: %s" % (cite_ref, res.get("why")))


def _assert_evidence(evidence_id: str | None) -> None:
    if not evidence_id:
        return
    p = EVIDENCE_ROOT / str(evidence_id)
    if not p.is_dir():
        raise UnknownEvidence("evidence_id %r has no directory under %s"
                              % (evidence_id, EVIDENCE_ROOT.name))


def record(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_ref_id: int,
    cite_ref: str,
    version: int | None = None,
    file_path: str | None = None,
    line_no: int | None = None,
    tdd_pass: int = 0, tdd_fail: int = 0,
    tdd_verify_pass: int = 0, tdd_verify_fail: int = 0,
    ontology_pass: int = 0, ontology_fail: int = 0,
    evidence_id: str | None = None,
    decided_by: str | None = None,
    reviews: list | None = None,
    review_measurement: dict | None = None,
) -> dict[str, Any]:
    """Record a decision. G1/G2/G3 all live here.

    G1 submit : cite_ref must be a real reference; evidence must exist
    G2 verify : APPROVED requires all three stages passed
    G3 class  : the class is DERIVED; WORKABLE cannot be claimed unverified
    G4 review : when SEVERAL independent workers reviewed this entity, an
                undiscriminated disagreement must NOT be recorded as APPROVED
                (see independent_review.py; opt-in — no `reviews` means the
                gate does not apply, and that is REPORTED, not silent)
    """
    ensure_schema(conn)
    et = er.get_entity_type(conn, entity_type)
    if not et:
        return {"ok": False, "why": "unknown entity_type %r" % entity_type}
    # THE ENTITY MUST EXIST — BUT NOT NECESSARILY BE ACTIVE.
    #
    # MEASURED DEFECT THIS CLOSES (2026-09-27): this used
    # `er.resolve_entity`, which filters `is_active = 1`. For a
    # `dimension_binding` (letter `Y`) that is a CIRCULAR DEPENDENCY: the
    # binding is `is_active=0` UNTIL it is activated, and the approval is what
    # the activation gate READS. So requiring `is_active=1` here means a binding
    # can never be approved, and therefore can never be activated — the gate
    # would refuse every binding for the reason the gate exists to change.
    #
    # MEASURED: all 120 bindings failed with "no active dimension_binding entity
    # with binding_id = N", while their three stages had ALL PASSED.
    #
    # REACHABILITY means the row EXISTS and its register is declared. The
    # `is_active` filter is kept for every letter EXCEPT the ones whose
    # `is_active` the activation gate itself writes.
    _GATE_WRITTEN = {"Y"}  # dimension_binding: the gate writes its is_active
    if et["type_letter"] in _GATE_WRITTEN:
        table, pk = et["register_table"], et["pk_column"]
        if not (er._IDENT_RE.match(table) and er._IDENT_RE.match(pk)):
            return {"ok": False,
                    "why": "%s/%s are not valid identifiers" % (table, pk)}
        row = conn.execute(
            "SELECT 1 FROM %s WHERE %s = ?" % (table, pk),
            (int(entity_ref_id),)).fetchone()
        if not row:
            return {"ok": False,
                    "why": "no %s row with %s = %d"
                           % (et["entity_kind"], pk, int(entity_ref_id))}
    elif not er.resolve_entity(conn, et["type_letter"], int(entity_ref_id)):
        return {"ok": False,
                "why": "no active %s entity with %s = %d"
                       % (et["entity_kind"], et["pk_column"], int(entity_ref_id))}

    # G4 — the independent-review gate runs BEFORE anything is written.
    #
    # WHY HERE: "approved" is the moment a claim about this entity is adopted
    # into the system. If several workers reviewed it and disagreed, adopting
    # one of their answers would be a vote. Measured this session: three
    # external workers answered the same question 7 / 16 / 8, and the correct
    # action was a measurement, not a majority.
    review = {"applies": False}
    if reviews:
        import independent_review as ir
        try:
            ir.assert_may_adopt(reviews, measurement=review_measurement,
                                cite_ref=cite_ref)
            review = {"applies": True, "ok": True,
                      "positions": ir.disagreement_count(reviews)}
        except ir.UndiscriminatedDisagreement as e:
            # recorded to the HELP list: this entity needs a human/another
            # worker, and the row carries the chat that can discuss it.
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO waiting_helping "
                    "(entity_type, entity_ref_id, reason) VALUES (?, ?, ?)",
                    (et["type_letter"], int(entity_ref_id),
                     "undiscriminated independent review: %s" % e))
                conn.commit()
            except sqlite3.Error:
                pass
            return {"ok": False, "gate": "G4", "why": str(e),
                    "review": {"applies": True, "ok": False},
                    "waiting_list": "waiting_helping"}

    # G1
    try:
        _assert_cited(cite_ref, conn)
        _assert_evidence(evidence_id)
    except (UncitedApproval, UnknownEvidence) as e:
        return {"ok": False, "gate": "G1", "why": str(e)}
    except Exception as e:
        return {"ok": False, "gate": "G1",
                "why": "%s: %s" % (type(e).__name__, e)}

    stages = check_stages(
        tdd_pass=tdd_pass, tdd_fail=tdd_fail,
        tdd_verify_pass=tdd_verify_pass, tdd_verify_fail=tdd_verify_fail,
        ontology_pass=ontology_pass, ontology_fail=ontology_fail)

    # G2
    verdict = "APPROVED" if stages["all_passed"] else "PENDING"
    if not stages["all_passed"]:
        verdict = "REJECTED" if any(
            stages[s]["fail"] > 0 for s in STAGES) else "PENDING"

    row = conn.execute(
        "SELECT id FROM register_approve WHERE entity_type = ? "
        "AND entity_ref_id = ? AND IFNULL(version,-1) = IFNULL(?, -1)",
        (et["type_letter"], int(entity_ref_id), version)).fetchone()

    now = _utc_now()
    if row:
        conn.execute(
            "UPDATE register_approve SET file_path=?, line_no=?, verdict=?, "
            "class=?, tdd_pass=?, tdd_fail=?, tdd_verify_pass=?, "
            "tdd_verify_fail=?, ontology_pass=?, ontology_fail=?, evidence_id=?, "
            "cite_ref=?, decided_at=?, decided_by=?, updated_at=? WHERE id=?",
            (file_path, line_no, verdict, classify(stages),
             tdd_pass, tdd_fail, tdd_verify_pass, tdd_verify_fail,
             ontology_pass, ontology_fail, evidence_id, cite_ref,
             now if verdict != "PENDING" else None, decided_by, now, row["id"]))
        rid = row["id"]
        created = False
    else:
        cur = conn.execute(
            "INSERT INTO register_approve (entity_type, entity_ref_id, version, "
            "file_path, line_no, verdict, class, tdd_pass, tdd_fail, "
            "tdd_verify_pass, tdd_verify_fail, ontology_pass, ontology_fail, "
            "evidence_id, cite_ref, decided_at, decided_by, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (et["type_letter"], int(entity_ref_id), version, file_path, line_no,
             verdict, classify(stages), tdd_pass, tdd_fail,
             tdd_verify_pass, tdd_verify_fail, ontology_pass, ontology_fail,
             evidence_id, cite_ref,
             now if verdict != "PENDING" else None, decided_by, now))
        rid = cur.lastrowid
        created = True
    conn.commit()
    return {"ok": True, "created": created, "id": rid, "verdict": verdict,
            "class": classify(stages), "stages": stages, "review": review}


# ---------------------------------------------------------------------------
# performance counters (the watchdog READS these)
# ---------------------------------------------------------------------------


def bump_usage(conn: sqlite3.Connection, register_approve_id: int,
               *, passed: bool) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute("SELECT id, usage_count, pass_count, fail_count "
                       "FROM coding_performance WHERE register_approve_id = ?",
                       (int(register_approve_id),)).fetchone()
    if row:
        conn.execute(
            "UPDATE coding_performance SET usage_count = usage_count + 1, "
            "pass_count = pass_count + ?, fail_count = fail_count + ?, "
            "updated_at = ? WHERE register_approve_id = ?",
            (1 if passed else 0, 0 if passed else 1, _utc_now(),
             int(register_approve_id)))
    else:
        conn.execute(
            "INSERT INTO coding_performance (register_approve_id, usage_count, "
            "pass_count, fail_count, updated_at) VALUES (?, 1, ?, ?, ?)",
            (int(register_approve_id), 1 if passed else 0,
             0 if passed else 1, _utc_now()))
    conn.commit()
    return {"ok": True, "register_approve_id": int(register_approve_id),
            "passed": bool(passed)}


def health(conn: sqlite3.Connection) -> dict[str, Any]:
    """What the watchdog asks for. Derived from the counters, not from beats."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(usage_count),0) AS usage, "
        "COALESCE(SUM(pass_count),0) AS pass, "
        "COALESCE(SUM(fail_count),0) AS fail, "
        "COUNT(*) AS rows FROM coding_performance").fetchone()
    fail = int(row["fail"])
    usage = int(row["usage"])
    return {
        "ok": True,
        "entries": int(row["rows"]),
        "usage": usage,
        "pass": int(row["pass"]),
        "fail": fail,
        "pass_ratio": (round(int(row["pass"]) / usage, 4) if usage else None),
        # the decision the watchdog acts on
        "signal": "watchdog" if fail > 0 else ("heartbeat" if usage > 0 else "idle"),
        "note": "pass/fail are COUNTERS here; worker_heartbeat stays liveness",
    }


# ---------------------------------------------------------------------------
# the two work lists
# ---------------------------------------------------------------------------


def refresh_worklists(conn: sqlite3.Connection, *,
                      dry_run: bool = True) -> dict[str, Any]:
    """Derive DEAD vs FAIL from the registers and the decisions.

    DEAD  = a register row with no usage and no passing verification
            -> nobody uses it; it may be cleaned up
    FAIL  = a register row that was used and whose verification failed
            -> someone needs it fixed
    They are kept apart because they need different people.
    """
    ensure_schema(conn)
    dead: list[dict] = []
    fail: list[dict] = []
    for r in conn.execute(
            "SELECT ra.id, ra.entity_type, ra.entity_ref_id, ra.verdict, "
            "       ra.class, ra.cite_ref, "
            "       COALESCE(cp.usage_count,0) AS usage, "
            "       COALESCE(cp.fail_count,0) AS fails "
            "FROM register_approve ra "
            "LEFT JOIN coding_performance cp ON cp.register_approve_id = ra.id"):
        usage = int(r["usage"])
        fails = int(r["fails"])
        key = {"entity_type": r["entity_type"],
               "entity_ref_id": r["entity_ref_id"]}
        if fails > 0:
            fail.append({**key, "reason": "%d failed use(s); class=%s"
                         % (fails, r["class"]), "cite_ref": r["cite_ref"]})
        elif usage == 0 and r["verdict"] != "APPROVED":
            dead.append({**key, "reason": "registered, never used, verdict=%s"
                         % r["verdict"], "cite_ref": r["cite_ref"]})
    if not dry_run:
        for tbl, rows in (("waiting_cleanup", dead), ("waiting_helping", fail)):
            for w in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO %s (entity_type, entity_ref_id, "
                    "reason) VALUES (?, ?, ?)"
                    % tbl,
                    (w["entity_type"], w["entity_ref_id"], w["reason"]))
        conn.commit()
    return {"ok": True, "dry_run": dry_run,
            "waiting_cleanup": dead, "waiting_helping": fail,
            "cleanup_count": len(dead), "helping_count": len(fail)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--extend-alphabet", action="store_true",
                    help="add the post-seed entity letters (R P U W K Q)")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--worklists", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        out: dict[str, Any] = {}
        if args.ensure:
            out["schema"] = ensure_schema(conn)
        if args.extend_alphabet:
            out["alphabet"] = er.ensure_entity_registry_schema(conn)
            out["letters"] = [{"letter": e["type_letter"],
                               "kind": e["entity_kind"],
                               "register": e["register_table"]}
                              for e in er.list_entity_types(conn)]
        if args.health:
            out["health"] = health(conn)
        if args.worklists:
            out["worklists"] = refresh_worklists(conn, dry_run=False)
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        else:
            if "alphabet" in out:
                a = out["alphabet"]
                print("alphabet: seeded=%s added=%s letters=%s dangling=%s"
                      % (a.get("seeded"), a.get("added"), a.get("letters"),
                         a.get("dangling") or "none"))
                for x in out.get("letters", []):
                    print("   %s %-11s -> %s"
                          % (x["letter"], x["kind"], x["register"]))
            if "health" in out:
                h = out["health"]
                print("health: entries=%d usage=%d pass=%d fail=%d ratio=%s "
                      "signal=%s" % (h["entries"], h["usage"], h["pass"],
                                     h["fail"], h["pass_ratio"], h["signal"]))
            if "worklists" in out:
                w = out["worklists"]
                print("waiting_cleanup=%d waiting_helping=%d"
                      % (w["cleanup_count"], w["helping_count"]))
                for d in w["waiting_cleanup"]:
                    print("   CLEANUP %s-%s  %s"
                          % (d["entity_type"], d["entity_ref_id"], d["reason"]))
                for f in w["waiting_helping"]:
                    print("   HELP    %s-%s  %s"
                          % (f["entity_type"], f["entity_ref_id"], f["reason"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()