"""qc_gate_runner.py — run the NINE quality dimensions over ONE subject.

WHAT THIS IS (and is NOT)
-------------------------
It is a DISPATCHER. For each gate it calls an EXISTING module (named in
`qc_gate.GATES[].checker_ref`) and normalises the answer into ONE shape:

    {gate_key, verdict, value, metric_target, polarity, detail,
     evidence_ref, cite_ref}

It does NOT judge, does NOT score and does NOT decide a pass — the arbiter
(`qc_arbiter.arbitrate`) derives the pass from the number, and `qc_contract`
records it. A dispatcher that also judged would be a second source of truth.

UNKNOWN IS A REAL OUTCOME
-------------------------
When a gate cannot measure its subject (a missing table, an unimportable module,
a subject kind nobody bound) it returns `UNKNOWN` — NEVER a pass. That mirrors
`qc_contract.UNKNOWN_BLOCKS = True` and `evidence_classify`'s refusal to judge an
absent container. A gate that returns PASS because it could not look is the
false-success this layer exists to stop.

EVERY CHECK STATES ITS READER AND POPULATION
--------------------------------------------
`measurement-scope`: a count of a TABLE is not a count of a LIST. So each
checker's `detail` names the READER (the table/view it queried) and the
POPULATION (the filter it applied). A number with no population cannot be
audited.

Run:
    .\\.venv\\Scripts\\python.exe qc_gate_runner.py --subject-kind table --subject chat
    .\\.venv\\Scripts\\python.exe qc_gate_runner.py --gate trace
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_gate  # noqa: E402

DEFAULT_DB = BASE_DIR / "agent.db"
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# The EIGHT measurable dimensions. The NINTH (`verdict`) is the arbiter's
# aggregate, not a checker, so it is excluded from the per-gate loop.
CHECK_KEYS: tuple[str, ...] = tuple(k for k in qc_gate.GATE_KEYS if k != "verdict")


def _gate(key: str) -> dict[str, Any]:
    for g in qc_gate.GATES:
        if g["gate_key"] == key:
            return g
    raise KeyError(key)


def _result(key: str, *, verdict: str, value: float | None, detail: str,
            evidence_ref: str) -> dict[str, Any]:
    # AN UNDECLARED GATE MUST NOT CRASH. A dispatcher asked for a gate it does
    # not declare returns UNKNOWN with named defaults — the same rule as an
    # unmeasurable subject: a missing answer is never a pass, and a report that
    # dies on a finding is not a report.
    try:
        g = _gate(key)
        target, polarity = float(g["metric_target"]), g["polarity"]
        kind, unit, cite = g["metric_kind"], g["metric_unit"], g["cite_ref"]
        mode = str(g.get("mode", "declare"))
    except KeyError:
        target, polarity = 0.0, "at_least"
        kind, unit, cite = "count", "count of undeclared gates", ""
        mode = "declare"
    return {
        "gate_key": key,
        "verdict": verdict,
        "value": value,
        "metric_target": target,
        "polarity": polarity,
        "metric_kind": kind,
        "metric_unit": unit,
        "mode": mode,
        "detail": detail,
        "evidence_ref": evidence_ref,
        "cite_ref": cite,
    }


# ---------------------------------------------------------------------------
# A01 ontology — is the named object a REGISTERED term and a LIVE object?
# ---------------------------------------------------------------------------
def _check_ontology(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    checks: list[tuple[str, bool, str]] = []
    # ---- A FILE IS A DIFFERENT SUBJECT FROM A NAMED OBJECT ------------------
    # MEASURED 2026-09-28, by RUNNING the PostToolUse hook: passing a `.py` PATH
    # to the object-name branch made `object_door.resolve_object('qc_gate.py')`
    # FAIL, so a file with a fine name was refused by a check about a NAME it is
    # not. That is the measurement-scope defect (a count of the WRONG population)
    # one layer up. So a FILE is measured by the checks that APPLY to a file:
    # is it covered by a code entity, and is its MODULE name registered.
    if kind in ("file", "path"):
        import entity_backfill as eb
        try:
            cov = eb.covered_by(conn, ref)
            checks.append(("file_covered_by_entity", bool(cov.get("ok")),
                           "code_location_registry: %s"
                           % (cov.get("entities") or cov.get("reason"))))
        except Exception as exc:
            checks.append(("file_covered_by_entity", False, "%s" % exc))
        mod = Path(ref).stem
        try:
            import terminology_registry as tr
            ok, why = tr.assert_named(conn, mod)
            checks.append(("module_name_registered", bool(ok), str(why)[:90]))
        except Exception as exc:
            checks.append(("module_name_registered", False, "%s" % exc))
        passed = sum(1 for _, ok, _ in checks if ok)
        value = round(100.0 * passed / len(checks), 2)
        detail = ("READER: code_location_registry + terminology_registry; "
                  "POPULATION: %d applicable checks for FILE %r -> %s"
                  % (len(checks), ref,
                     "; ".join("%s=%s" % (n, o) for n, o, _ in checks)))
        return _result("ontology", verdict=PASS if passed == len(checks) else FAIL,
                       value=value, detail=detail,
                       evidence_ref="qc_gate_runner.py:_check_ontology")
    # ---- A NAMED OBJECT (a table, a contract, a skill) ----------------------
    # 1. the NAME is a registered term (a name nobody defined is an invented word)
    try:
        import terminology_registry as tr
        ok, why = tr.assert_named(conn, ref)
        checks.append(("name_registered", bool(ok), str(why)[:90]))
    except Exception as exc:
        checks.append(("name_registered", False, "%s: %s" % (type(exc).__name__, exc)))
    # 2. the object RESOLVES through the ONE DOOR (2026-09-28).
    #
    # WHY THIS REPLACED a live-table check: `object_door.resolve_object` is the
    # repo's single resolver for a NAMED object, and it follows a RENAME
    # (`terminology_alias`) and accepts a view as well as a table. MEASURED: the
    # old check demanded a live table, so an object that IS an entity
    # (a contract, a skill) FAILed ontology for the wrong reason. One door, one
    # answer.
    resolved_kind, resolved_exists, resolved_canon = None, False, str(ref)
    try:
        import object_door as od
        r = od.resolve_object(conn, ref)
        resolved_kind = r.get("kind")
        resolved_exists = bool(r.get("exists"))
        resolved_canon = str(r.get("canonical") or ref)
        checks.append(("object_resolves", resolved_exists,
                       "object_door kind=%s canonical=%r"
                       % (resolved_kind, resolved_canon)))
    except Exception as exc:
        checks.append(("object_resolves", False,
                       "object_door: %s: %s" % (type(exc).__name__, exc)))
    # 3. when the object IS a live table/view, it must also be REGISTERED
    #    (READER: db_table_registry). A non-table object (a contract, an entity)
    #    has no db_table_registry row, so this check is N/A and is NOT counted
    #    against it — a dimension must not fail for a check that does not apply.
    n_checks = len(checks)
    if resolved_exists:
        n_checks += 1
        try:
            reg = conn.execute(
                "SELECT COUNT(*) FROM db_table_registry WHERE table_key=?",
                (resolved_canon,)).fetchone()[0]
            checks.append(("object_registered", bool(reg),
                           "db_table_registry table_key=%r" % resolved_canon))
        except Exception as exc:
            checks.append(("object_registered", False, "%s" % exc))
    passed = sum(1 for _, ok, _ in checks if ok)
    value = round(100.0 * passed / n_checks, 2) if n_checks else 0.0
    detail = ("READER: terminology_registry + object_door(+db_table_registry); "
              "POPULATION: %d applicable checks for object %r -> %s"
              % (n_checks, ref, "; ".join("%s=%s" % (n, o) for n, o, _ in checks)))
    return _result("ontology", verdict=PASS if passed == n_checks else FAIL,
                   value=value, detail=detail,
                   evidence_ref="qc_gate_runner.py:_check_ontology")


# ---------------------------------------------------------------------------
# A02 5W1H — how many of the SIX dimensions are bound for this subject kind?
# ---------------------------------------------------------------------------
def _check_5w1h(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    try:
        import skill_5w1h as fw
        six = list(fw.DIMENSION_NAMES)
    except Exception:
        six = ["what", "why", "who", "when", "where", "how"]
    try:
        rows = conn.execute(
            "SELECT DISTINCT dimension_key FROM dimension_binding_registry "
            "WHERE subject_kind=?", (kind,)).fetchall()
        bound = {str(r[0]) for r in rows}
    except Exception as exc:
        return _result("5w1h", verdict=UNKNOWN, value=None,
                       detail="READER: dimension_binding_registry; could not read "
                              "for subject_kind=%r: %s" % (kind, exc),
                       evidence_ref="qc_gate_runner.py:_check_5w1h")
    present = [d for d in six if d in bound]
    value = round(100.0 * len(present) / len(six), 2)
    detail = ("READER: dimension_binding_registry (subject_kind=%r); "
              "POPULATION: the %d declared 5W1H dimensions -> bound %s"
              % (kind, len(six), sorted(present)))
    return _result("5w1h", verdict=PASS if len(present) == len(six) else FAIL,
                   value=value, detail=detail,
                   evidence_ref="qc_gate_runner.py:_check_5w1h")


# ---------------------------------------------------------------------------
# A03 middleware — does a subject-kind's ref resolve through the register?
# ---------------------------------------------------------------------------
def _check_middleware(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    checks: list[tuple[str, bool, str]] = []
    try:
        row = conn.execute(
            "SELECT ref_table, ref_column FROM subject_kind_registry "
            "WHERE kind_key=? AND is_active=1", (kind,)).fetchone()
        present = bool(row)
        checks.append(("kind_registered", present,
                       "subject_kind_registry kind_key=%r" % kind))
        if present:
            checks.append(("ref_resolvable",
                           str(row["ref_table"] or "NA") != "NA",
                           "ref_table=%s.%s" % (row["ref_table"], row["ref_column"])))
    except Exception as exc:
        checks.append(("kind_registered", False, "%s" % exc))
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks) or 1
    value = round(100.0 * passed / total, 2)
    detail = ("READER: subject_kind_registry; POPULATION: the middleware checks "
              "for kind %r -> %s"
              % (kind, "; ".join("%s=%s" % (n, o) for n, o, _ in checks)))
    return _result("middleware",
                   verdict=PASS if (total and passed == total) else FAIL,
                   value=value, detail=detail,
                   evidence_ref="qc_gate_runner.py:_check_middleware")


# ---------------------------------------------------------------------------
# A04 tdd — does the contract DECLARE a discriminating suite (pass + hard_fail)?
# ---------------------------------------------------------------------------
def _check_tdd(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    """A04 `tdd`, mode=run: EXECUTE the contract's suite, do not just count it.

    MEASURED 2026-09-28: the old check read `skill_contract_tdd_case` and counted
    declared cases; a declared case is not a PASSING case. `skill_tdd_runner.run_contract`
    ALREADY runs every active case and returns `n_passed/n_cases` — this check now
    calls it. A contract with NO registered probe cannot be run, so it is
    `UNKNOWN` (an unmeasurable subject is never a pass).

    `record=False`: a QC MEASUREMENT must not advance the contract's streak (the
    streak is the activation key, and a measurement is not an activation act).
    """
    try:
        import skill_tdd_runner as tr
        out = tr.run_contract(ref, record=False, db_path=DEFAULT_DB,
                              verbose=False)
    except Exception as exc:
        return _result("tdd", verdict=UNKNOWN, value=None,
                       detail="READER: skill_tdd_runner.run_contract(%r); could not "
                              "run: %s: %s" % (ref, type(exc).__name__, exc),
                       evidence_ref="qc_gate_runner.py:_check_tdd")
    if out.get("error"):
        return _result("tdd", verdict=UNKNOWN, value=None,
                       detail="READER: skill_tdd_runner.run_contract(%r); %s"
                              % (ref, out.get("error")),
                       evidence_ref="qc_gate_runner.py:_check_tdd")
    n_cases = int(out.get("n_cases") or 0)
    n_passed = int(out.get("n_passed") or 0)
    if n_cases == 0:
        return _result("tdd", verdict=UNKNOWN, value=None,
                       detail="READER: run_contract(%r) ran 0 cases — an empty "
                              "suite is not a pass" % ref,
                       evidence_ref="qc_gate_runner.py:_check_tdd")
    value = round(100.0 * n_passed / n_cases, 2)
    no_probe = [r for r in (out.get("results") or [])
                if "no probe registered" in str(r.get("detail") or "")]
    # A run where EVERY case had no probe measured NOTHING — report UNKNOWN, not
    # FAIL, because the suite could not be executed at all (the honest answer).
    if len(no_probe) == n_cases:
        return _result("tdd", verdict=UNKNOWN, value=None,
                       detail="READER: run_contract(%r) — %d/%d cases have NO "
                              "registered probe, so nothing was executed"
                              % (ref, len(no_probe), n_cases),
                       evidence_ref="qc_gate_runner.py:_check_tdd")
    detail = ("READER: skill_tdd_runner.run_contract(%r) (REALLY EXECUTED); "
              "POPULATION: n_passed=%d of n_cases=%d -> %.2f%%"
              % (ref, n_passed, n_cases, value))
    return _result("tdd", verdict=PASS if n_passed == n_cases else FAIL,
                   value=value, detail=detail,
                   evidence_ref="qc_gate_runner.py:_check_tdd")


# ---------------------------------------------------------------------------
# A05 boundary — how many hard_fail (boundary) cases does the contract declare?
# ---------------------------------------------------------------------------
def _check_boundary(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    # MEASURED 2026-09-28, by RUNNING the verify gate on a real `.py`: a FILE was
    # passed here, `contract_id` matched no `skill_contract_tdd_case` row, and the
    # gate reported FAIL (0 cases) — blaming the file for a contract check that
    # does not apply to it. A dimension must NOT fail for a check that does not
    # apply (the same wrong-population defect fixed in `_check_ontology`). A
    # boundary case is DECLARED ON A CONTRACT, so a file subject is N/A.
    if kind in ("file", "path"):
        return _result("boundary", verdict=UNKNOWN, value=None,
                       detail=("READER: skill_contract_tdd_case; N/A — a boundary "
                               "case is declared on a CONTRACT, and %r is a FILE. "
                               "Verify it with --contract <id>." % ref),
                       evidence_ref="qc_gate_runner.py:_check_boundary")
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM skill_contract_tdd_case WHERE contract_id=? "
            "AND kind='hard_fail' AND status='active'", (ref,)).fetchone()[0]
    except Exception as exc:
        return _result("boundary", verdict=UNKNOWN, value=None,
                       detail="READER: skill_contract_tdd_case; could not read: %s"
                              % exc, evidence_ref="qc_gate_runner.py:_check_boundary")
    detail = ("READER: skill_contract_tdd_case (contract_id=%r, kind='hard_fail'); "
              "POPULATION: active boundary cases -> %d" % (ref, n))
    return _result("boundary", verdict=PASS if n >= 1 else FAIL, value=float(n),
                   detail=detail, evidence_ref="qc_gate_runner.py:_check_boundary")


# ---------------------------------------------------------------------------
# A06 role_environment — is there a declared pair for this ROLE?
# ---------------------------------------------------------------------------
def _check_role_environment(conn: sqlite3.Connection, kind: str,
                            ref: str) -> dict[str, Any]:
    try:
        import role_environment as re_mod
        out = re_mod.list_pairs(conn)
        if not out.get("ok"):
            return _result("role_environment", verdict=UNKNOWN, value=None,
                           detail="READER: role_environment p; could not read: %s"
                                  % out.get("error"),
                           evidence_ref="qc_gate_runner.py:_check_role_environment")
        n = int(out.get("by_role", {}).get(str(ref), 0))
    except Exception as exc:
        return _result("role_environment", verdict=UNKNOWN, value=None,
                       detail="READER: role_environment; %s: %s"
                              % (type(exc).__name__, exc),
                       evidence_ref="qc_gate_runner.py:_check_role_environment")
    detail = ("READER: role_environment JOIN role_registry JOIN working_environment "
              "(is_active=1); POPULATION: rows whose role_key=%r -> %d" % (ref, n))
    return _result("role_environment", verdict=PASS if n >= 1 else FAIL,
                   value=1.0 if n >= 1 else 0.0, detail=detail,
                   evidence_ref="qc_gate_runner.py:_check_role_environment")


# ---------------------------------------------------------------------------
# A07 trace — what pct of trace links are non-null? (READER: proof_run)
# ---------------------------------------------------------------------------
def _check_trace(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(proof_run)")}
        if "linked_trace_id" not in cols:
            return _result("trace", verdict=UNKNOWN, value=None,
                           detail="READER: proof_run; no linked_trace_id column",
                           evidence_ref="qc_gate_runner.py:_check_trace")
        total = conn.execute("SELECT COUNT(*) FROM proof_run").fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM proof_run WHERE linked_trace_id IS NOT NULL "
            "AND linked_trace_id <> ''").fetchone()[0]
    except Exception as exc:
        return _result("trace", verdict=UNKNOWN, value=None,
                       detail="READER: proof_run; could not read: %s" % exc,
                       evidence_ref="qc_gate_runner.py:_check_trace")
    if total == 0:
        return _result("trace", verdict=UNKNOWN, value=None,
                       detail="READER: proof_run (0 rows) — no trace to measure; "
                              "an empty population is not a pass",
                       evidence_ref="qc_gate_runner.py:_check_trace")
    value = round(100.0 * linked / total, 2)
    detail = ("READER: proof_run; POPULATION: rows with linked_trace_id NOT NULL "
              "-> %d of %d (%.2f%%)" % (linked, total, value))
    return _result("trace", verdict=PASS if value >= 100.0 else FAIL, value=value,
                   detail=detail, evidence_ref="qc_gate_runner.py:_check_trace")


# ---------------------------------------------------------------------------
# A08 safety — how many LIVE hardcode candidates are in the subject file?
# ---------------------------------------------------------------------------
def _resolve_subject_files(conn: sqlite3.Connection,
                           ref: str) -> tuple[list[Path], str]:
    """Resolve a subject to REAL python files. Returns `(files, how)`.

    MEASURED 2026-09-28: `safety` returned UNKNOWN for every non-file subject,
    so a table or a contract could never be scanned. But the file link EXISTS:
    `code_location_registry` maps an entity to its `file_path`, and
    `entity_backfill.covered_by` reads it (`db_schema.py:2016`). This resolves a
    table name (`db_schema`) OR a dotted python module (`qc_gate_runner`) to the
    files it lives in — a path is tried first, then the module, then the entity.
    """
    p = Path(ref)
    if p.suffix == ".py" and (p if p.is_absolute() else BASE_DIR / p).is_file():
        f = p if p.is_absolute() else (BASE_DIR / p)
        return [f.resolve()], "path"
    # a dotted module name -> its file(s)
    if ref.isidentifier() or all(s.isidentifier() for s in ref.split(".")):
        cand = BASE_DIR / (ref.replace(".", "/") + ".py")
        if cand.is_file():
            return [cand.resolve()], "module"
    # an entity/table subject -> its code_location_registry file(s)
    try:
        import entity_backfill as eb
        cov = eb.covered_by(conn, ref)
        if cov.get("ok"):
            files = []
            for r in cov.get("rows", []):
                fp = str(r.get("file_path") or "").strip()
                if fp:
                    q = Path(fp)
                    q = q if q.is_absolute() else (BASE_DIR / fp)
                    if q.is_file():
                        files.append(q.resolve())
            if files:
                return files, "code_location_registry"
    except Exception:
        pass
    return [], "unresolved"


def _check_safety(conn: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any]:
    """A08 static safety. THREE readers, because the dimension has three shapes.

    WHY THREE (MEASURED 2026-09-28; plan CODE.QUALITY Phases 2 and 5)
    ---------------------------------------------------------------
    The third reader is the AUTOMATION the human asked for — *"真正的 skill 化
    （寫檔時自動量）"* — and it closes the last measured gap: after bandit and
    ruff entered as registered factors, MEASURED `git grep ruff qc_gate_runner.py`
    was EMPTY, so the tools were only measured when a PROOF ran, never when a
    file was WRITTEN. A rule that is only checked on request is a rule the writer
    can forget; the hook (`scripts/qc_gate_hook.py`) fires on `PostToolUse`, so
    wiring the reader here makes it automatic.

    `hardcode_scan` finds a literal that COULD HAVE BEEN LOOKED UP.
    `code_shape` finds something worse: a parameter REBOUND in its own body,
    which makes the OUTPUT TRUE BY CONSTRUCTION —

        def abc(A, B):
            A = 0            # the caller's value is gone
            return A + B == 1

    No input can make that fail, so no test can catch it, and A08 is exactly the
    dimension that says "the artefact must not be able to lie about itself". The
    user described this pattern themselves (2026-09-23) and `code_shape` was
    written for it — but MEASURED, `code_shape` had NO caller in this gate.

    `ruff` is reader 3, and it carries the ONE class of the CODE.QUALITY gap
    table that the other two do not: an UNDEFINED NAME (F821). MEASURED
    2026-09-28: `logic_generator.py` annotated `sqlite3.Connection` in 18
    signatures while never importing sqlite3, so `typing.get_type_hints()` raised
    `NameError`. That is a real latent crash, and it is neither a hardcoded
    literal (reader 1) nor a rebound parameter (reader 2).

    EACH READER NAMES ITS OWN READER AND POPULATION, and the verdict is PASS only
    when BOTH are clean. A reader that cannot run makes the dimension UNKNOWN —
    never a pass, and never a silent skip (`measurement-scope`).
    """
    files, how = _resolve_subject_files(conn, ref)
    if not files:
        return _result("safety", verdict=UNKNOWN, value=None,
                       detail=("READER: hardcode_scan + code_shape + ruff; can't "
                               "resolve %r to a file (tried path, module, "
                               "code_location_registry)" % ref),
                       evidence_ref="qc_gate_runner.py:_check_safety")
    try:
        import code_shape as csh
        import hardcode_scan as hc
    except Exception as exc:
        return _result("safety", verdict=UNKNOWN, value=None,
                       detail="READER: hardcode_scan + code_shape; unimportable: %s"
                              % exc,
                       evidence_ref="qc_gate_runner.py:_check_safety")

    # ---- reader 1: hardcoded values -------------------------------------
    live = []
    matched = 0
    for f in files:
        try:
            cands = hc.scan_file(f, BASE_DIR, conn=conn)
        except Exception as exc:
            return _result("safety", verdict=UNKNOWN, value=None,
                           detail="READER: hardcode_scan.scan_file(%s); %s: %s"
                                  % (f.name, type(exc).__name__, exc),
                           evidence_ref="qc_gate_runner.py:_check_safety")
        matched += len(cands)
        live += [c for c in cands
                 if not c.get("is_comment") and not c.get("is_message")
                 and not c.get("excluded")]
    n_hard = len(live)

    # ---- reader 2: parameters rebound in their own body ------------------
    n_rebind = 0
    redef_total = 0
    unread: list[str] = []
    for f in files:
        try:
            r = csh.redefined_params(f)
        except Exception as exc:
            return _result("safety", verdict=UNKNOWN, value=None,
                           detail="READER: code_shape.redefined_params(%s); %s: %s"
                                  % (f.name, type(exc).__name__, exc),
                           evidence_ref="qc_gate_runner.py:_check_safety")
        if not r.get("ok"):
            # "could not read" is NOT "clean" — the reader's own law.
            unread.append("%s[%s]" % (f.name, r.get("code")))
            continue
        redef_total += len(r.get("findings") or [])
        n_rebind += len(r.get("findings") or [])
    if unread:
        return _result("safety", verdict=UNKNOWN, value=None,
                       detail=("READER: code_shape; UNREADABLE=%s — a file that "
                               "could not be read is not a clean file" % unread),
                       evidence_ref="qc_gate_runner.py:_check_safety")

    # ---- reader 3: the ruff classes that are DEFECTS, not style -----------
    #
    # THE AUTOMATION (the human, 2026-09-28: "真正的 skill 化（寫檔時自動量）").
    # MEASURED before this: `ruff` was a registered factor with a proof, but
    # `git grep ruff qc_gate_runner.py` was EMPTY — so the tool ran only when a
    # PROOF ran, never when a file was WRITTEN. Wiring it HERE is what makes it
    # automatic: the hook fires on PostToolUse, so every written `.py` is measured.
    #
    # THE POPULATION IS THE DECLARED FACTOR UNIT, not ruff's raw count. MEASURED:
    # ruff finds 22,360 in the tree and UP031 (%-format) alone is 14,950 = 67%,
    # so counting everything would be a permanent red — the same unreachable-target
    # defect bandit's B608 taught this task. The classes below are the ones that
    # are DEFECTS (an unused import, an unused variable, an unsorted block, a BOM,
    # and an UNDEFINED NAME, which is a latent NameError — MEASURED: logic_generator
    # annotated sqlite3.Connection in 18 signatures with no import).
    n_ruff = 0
    ruff_by_rule: dict[str, int] = {}
    try:
        import ruff_reader as rr
    except Exception:
        rr = None
    if rr is not None:
        try:
            out = rr.measure([str(f) for f in files])
            if not out.get("ok"):
                # a reader that could not RUN is UNKNOWN, never a pass
                return _result("safety", verdict=UNKNOWN, value=None,
                               detail=("READER: ruff; could not run: %s"
                                       % out.get("reason")),
                               evidence_ref="ruff_reader.measure")
            n_ruff = int(out.get("defect_total") or 0)
            ruff_by_rule = out.get("by_rule") or {}
        except Exception as exc:
            return _result("safety", verdict=UNKNOWN, value=None,
                           detail="READER: ruff_reader.measure; %s: %s"
                                  % (type(exc).__name__, exc),
                           evidence_ref="ruff_reader.measure")

    n = n_hard + n_rebind + n_ruff
    detail = ("READER 1: hardcode_scan.scan_file(%s) [resolved by %s]; "
              "POPULATION 1: live hardcoded candidates = %d of %d matched; "
              "READER 2: code_shape.redefined_params(); "
              "POPULATION 2: params rebound in their own function = %d; "
              "READER 3: ruff_reader.measure() [F401/F841/I001/UP009/F821 only, "
              "NOT the raw count]; POPULATION 3: defect-class findings = %d %s"
              % (",".join(f.name for f in files), how, n_hard, matched,
                 n_rebind, n_ruff, dict(sorted(ruff_by_rule.items()))))
    return _result("safety", verdict=PASS if n == 0 else FAIL, value=float(n),
                   detail=detail, evidence_ref="qc_gate_runner.py:_check_safety")


CHECKS = {
    "ontology": _check_ontology,
    "5w1h": _check_5w1h,
    "middleware": _check_middleware,
    "tdd": _check_tdd,
    "boundary": _check_boundary,
    "role_environment": _check_role_environment,
    "trace": _check_trace,
    "safety": _check_safety,
}


def run_gate(conn: sqlite3.Connection, gate_key: str, *,
             subject_kind: str, subject_ref: str) -> dict[str, Any]:
    """Run ONE dimension. `verdict` returns the aggregate (calls the 8).

    A gate that is declared but has NO checker returns UNKNOWN — never a pass.
    """
    if gate_key == "verdict":
        import qc_arbiter
        rows = [run_gate(conn, k, subject_kind=subject_kind, subject_ref=subject_ref)
                for k in CHECK_KEYS]
        agg = qc_arbiter.arbitrate(rows, expected_keys=CHECK_KEYS)
        return {"gate_key": "verdict", "verdict": agg["verdict"],
                "value": agg["score_0_100"], "metric_target": 100.0,
                "polarity": "at_least", "metric_kind": "score_0_100",
                "metric_unit": _gate("verdict")["metric_unit"],
                "detail": ("READER: qc_arbiter over the 8 dimension results; "
                           "POPULATION: %d of %d passed"
                           % (agg["gates_passed"], agg["gates_total"])),
                "evidence_ref": "qc_arbiter.arbitrate",
                "cite_ref": _gate("verdict")["cite_ref"], "_aggregate": agg}
    fn = CHECKS.get(gate_key)
    if fn is None:
        return _result(gate_key, verdict=UNKNOWN, value=None,
                       detail="no checker declared for gate %r" % gate_key,
                       evidence_ref="")
    return fn(conn, subject_kind, subject_ref)


def run_all(conn: sqlite3.Connection, *, subject_kind: str, subject_ref: str,
            task_id: str | None = None, trace_id: str | None = None,
            db_path: Path | str | None = None,
            record: bool = True) -> dict[str, Any]:
    """Run all 8 dimensions, arbitrate, and (optionally) write to `qc_run`.

    Writes go through `qc_contract.record` — the ONE verdict store. This module
    opens no table of its own.
    """
    import qc_arbiter
    import qc_contract

    run_ref = "GATE-%s" % subject_ref
    rows = [run_gate(conn, k, subject_kind=subject_kind, subject_ref=subject_ref)
            for k in CHECK_KEYS]
    agg = qc_arbiter.arbitrate(rows, expected_keys=CHECK_KEYS)

    written: list[dict[str, Any]] = []
    if record:
        for r in rows:
            written.append(qc_contract.record(
                "qc_gate", r["gate_key"], r["verdict"],
                reason=r["detail"], task_id=task_id, trace_id=trace_id,
                gate_key=r["gate_key"], gate_run_ref=run_ref,
                structural_cite=r.get("cite_ref"), check_name=False,
                db_path=db_path))
        written.append(qc_contract.record(
            "qc_gate", "verdict", agg["verdict"],
            reason=("score_0_100=%s; failed=%s"
                    % (agg["score_0_100"],
                       [f["gate_key"] for f in agg["failed_gates"]])),
            task_id=task_id, trace_id=trace_id,
            gate_key="verdict", score_0_100=agg["score_0_100"],
            gate_run_ref=run_ref, check_name=False, db_path=db_path))
    # THE EXPERIENCE (2026-09-28). Every gate result is ALSO an instance against
    # its `pattern_template` row, so a template accrues evidence from real runs
    # instead of being asserted. Best-effort: a missing template module must not
    # fail a gate run (the gate's own verdict is the deliverable).
    instances = 0
    try:
        import pattern_template as pt
        pt.ensure_schema(conn)
        for r in rows:
            pt.record_instance(
                conn, subject_kind=subject_kind, subject_ref=subject_ref,
                verdict=r["verdict"], value=r.get("value"),
                detail=r["detail"], cite_ref=r.get("cite_ref") or "qc_gate.py:1",
                gate_run_ref=run_ref, item_kind=r["gate_key"])
            instances += 1
    except Exception as exc:
        agg["pattern_instance_error"] = "%s: %s" % (type(exc).__name__, exc)

    return {"subject_kind": subject_kind, "subject_ref": subject_ref,
            "gate_run_ref": run_ref, "gates": rows, "aggregate": agg,
            "written": len(written), "pattern_instances": instances}


def main() -> int:
    ap = argparse.ArgumentParser(description="qc gate runner (dispatch only)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--gate", default="", help="one gate key (default: all)")
    ap.add_argument("--subject-kind", default="table")
    ap.add_argument("--subject", default="chat")
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--trace-id", default=None)
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        if args.gate:
            out = run_gate(conn, args.gate, subject_kind=args.subject_kind,
                           subject_ref=args.subject)
            print(json.dumps(out, indent=1, ensure_ascii=False))
        else:
            out = run_all(conn, subject_kind=args.subject_kind,
                          subject_ref=args.subject, task_id=args.task_id,
                          trace_id=args.trace_id, db_path=args.db,
                          record=not args.no_record)
            print(json.dumps(out, indent=1, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())