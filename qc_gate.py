# -*- coding: utf-8 -*-
"""qc_gate.py — the DECLARATION of the NINE quality dimensions, as DATA.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "9-Agent Quality Gate ... 係你整套架構唯一、統一、通用、全場適用嘅標準品質
     校驗總閘口"
    "totally review my site and have the plan by phase (name maybe not match
     terminontology, need to have full research and confirm with real data)"

MEASURED, and it is why this module WRITES NO CHECK ITSELF:

  * There is NO unified gate. `grep 'gate_registry|qc_dimension|quality_gate'`
    over the repo returns EMPTY. The nine dimensions exist ONLY as nine
    separate modules (`terminology_registry`, `skill_5w1h`,
    `identity_middleware`, `skill_tdd_runner`, `p1_boundary_test`,
    `role_environment`, `runtime_trace`, `hardcode_scan`, `register_approval`).
  * So the work is WIRING: one declaration (this file) + one runner
    (`qc_gate_runner`) + one arbiter (`qc_arbiter`). Nothing is re-implemented.

THE NAMES ARE LOOKED UP, NEVER INVENTED (the correction that made this plan)
---------------------------------------------------------------------------
    "it is BUG by worng speeling, as we have terminontology, i don't know why you
     still can keep find that / the name is 5W1H"

The human is right. A candidate key is NOT chosen in prose: it is looked up in
`terminology_registry`. MEASURED (`python terminology_registry.py --list`):
`ontology`, `5w1h`, `middleware`, `tdd`, `boundary`, `role`, `environment`,
`trace`, `safety`, `verdict`, `qc`, `gate` are ALL registered parts (active=1).
So every key below decomposes with **0 invented words**. `sast` and `arbiter`
are NOT registered and were therefore NOT used.

WHY A FIXED SET IS A PYTHON TUPLE, NOT TABLE ROWS
-------------------------------------------------
`skill_5w1h.py:47` states the rule and this module follows it:
    "the FIXED set ... is validated against a Python tuple ... a 7th cannot be
     introduced by an INSERT"

Nine is a fixed axiom. A table that could hold a tenth would let someone add a
dimension and silently change every verdict. So the tuple is the SSOT and
`qc_gate_registry` stores the per-gate DECLARATION (checker, metric, cite)
derived from it — the same two-layer shape `skill_factor_registry` uses for
factors.

Run:
    .\\.venv\\Scripts\\python.exe qc_gate.py --list
    .\\.venv\\Scripts\\python.exe qc_gate.py --check
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

DEFAULT_DB = BASE_DIR / "agent.db"

# The metric vocabulary is CLOSED and IMPORTED, not restated. `skill_factor`
# owns it (`MK_*`), so a second list here is the drift this repo has recorded.
from skill_factor import MK_BOOL, MK_COUNT, MK_PCT, MK_SCORE  # noqa: E402

METRIC_KINDS = (MK_BOOL, MK_COUNT, MK_PCT, MK_SCORE)

# A NUMBER ALONE CANNOT SAY WHICH DIRECTION PASSES: `count of hardcode candidates`
# passes at 0 (`at_most`) while `count of boundary cases` passes at >=1
# (`at_least`). Guessing the direction is how a gate reports PASS on a bad
# subject, so the polarity is DECLARED per gate.
POLARITIES = ("at_least", "at_most", "equal")

# HOW a gate measures. `declare` reads what is DECLARED; `run` EXECUTES the
# subject's own suite. MEASURED 2026-09-28: a count of declared test cases is not
# a measurement of whether they PASS, so `tdd` is `run`. Default `declare`.
MEASURE_MODES = ("declare", "run")

# ---------------------------------------------------------------------------
# THE NINE DIMENSIONS — the fixed set. `dimension` names the 5W1H dimension the
# gate reads (`runtime_trace.py` already reports factors in that one language),
# or 'NA' when the gate is about system structure rather than a 5W1H answer.
#
# `metric_unit` MUST match `"<kind> of <subject>"` — `factor_first_principle`'s
# rule: a number without a UNIT cannot be audited.
# ---------------------------------------------------------------------------
GATES: tuple[dict[str, Any], ...] = (
    dict(gate_key="ontology", sort_order=1,
         gate_name="Ontology — structural legality",
         dimension="what",
         checker_ref="qc_gate_runner._check_ontology.py",
         metric_kind=MK_PCT,
         metric_unit="pct of declared objects that are registered",
         metric_target=100.0, polarity="at_least",
         cite_ref="terminology_registry.py:1699"),
    dict(gate_key="5w1h", sort_order=2,
         gate_name="5W1H — completeness of the six dimensions",
         dimension="why",
         checker_ref="qc_gate_runner._check_5w1h.py",
         metric_kind=MK_PCT,
         metric_unit="pct of the six 5W1H dimensions bound for this subject kind",
         metric_target=100.0, polarity="at_least",
         cite_ref="skill_5w1h.py:47"),
    dict(gate_key="middleware", sort_order=3,
         gate_name="Middleware — data-flow contract",
         dimension="how",
         checker_ref="qc_gate_runner._check_middleware.py",
         metric_kind=MK_PCT,
         metric_unit="pct of contract checks that pass",
         metric_target=100.0, polarity="at_least",
         cite_ref="identity_middleware.py:1"),
    dict(gate_key="tdd", sort_order=4,
         gate_name="TDD — executable trustworthiness",
         dimension="how",
         checker_ref="qc_gate_runner._check_tdd.py",
         metric_kind=MK_PCT,
         metric_unit="pct of declared TDD cases that pass",
         metric_target=100.0, polarity="at_least",
         # `mode` DECLARES how the gate measures. `declare` reads what is
         # DECLARED (a count of cases); `run` EXECUTES the suite through
         # `skill_tdd_runner.run_contract`. MEASURED 2026-09-28: counting declared
         # cases is not the same as running them, and only the second is a
         # measurement of trustworthiness.
         mode="run",
         cite_ref="skill_tdd_runner.py:1"),
    dict(gate_key="boundary", sort_order=5,
         gate_name="Boundary — edge and stability risk",
         dimension="which",
         checker_ref="qc_gate_runner._check_boundary.py",
         metric_kind=MK_COUNT,
         metric_unit="count of boundary (hard_fail) cases declared",
         metric_target=1.0, polarity="at_least",
         cite_ref="p1_boundary_test.py:1"),
    dict(gate_key="role_environment", sort_order=6,
         gate_name="Role x Environment — permission pairing",
         dimension="who",
         checker_ref="qc_gate_runner._check_role_environment.py",
         metric_kind=MK_BOOL,
         metric_unit="boolean of whether a declared role x environment pair exists",
         metric_target=1.0, polarity="equal",
         cite_ref="role_environment.py:1"),
    dict(gate_key="trace", sort_order=7,
         gate_name="Trace — link completeness",
         dimension="when",
         checker_ref="qc_gate_runner._check_trace.py",
         metric_kind=MK_PCT,
         metric_unit="pct of trace links that are non-null",
         metric_target=100.0, polarity="at_least",
         cite_ref="runtime_trace.py:1"),
    dict(gate_key="safety", sort_order=8,
         gate_name="Safety — static scan of the subject file",
         dimension="where",
         checker_ref="qc_gate_runner._check_safety.py",
         metric_kind=MK_COUNT,
         metric_unit="count of live hardcode candidates in the subject file",
         metric_target=0.0, polarity="at_most",
         cite_ref="hardcode_scan.py:1"),
    dict(gate_key="verdict", sort_order=9,
         gate_name="Verdict — the arbiter (aggregate)",
         dimension="NA",
         checker_ref="qc_arbiter.arbitrate.py",
         metric_kind=MK_SCORE,
         metric_unit="score_0_100 of gate dimensions passing",
         metric_target=100.0, polarity="at_least",
         cite_ref="register_approval.py:67"),
)

GATE_KEYS: tuple[str, ...] = tuple(g["gate_key"] for g in GATES)

# The term this module is named for. Registered BEFORE use, because a name that
# is not a registered term makes every later reader pick the wrong referent.
TERM_KEY = "qc_gate"
TERM_DEFINITION = (
    "The universal quality gate layer: ONE runner that measures NINE fixed "
    "dimensions (ontology, 5W1H, middleware, tdd, boundary, role_environment, "
    "trace, safety, verdict) over any subject and writes ONE verdict to the "
    "existing qc_run table. It is NOT a new system: each dimension DELEGATES to "
    "an existing module, and it is NOT an agent — agent already names a worker."
)
TERM_CITE = "qc_gate.py:1"

# The letter this register would take as an entity, declared here so a proof can
# check it without re-typing the string.
REGISTER_TABLE = "qc_gate_registry"

# 🔴 THE TABLE DECLARATION WAS REMOVED FROM THIS MODULE (RING 5, D1, 2026-09-29).
#
# MEASURED: this constant WAS a second declaration of `qc_gate_registry` and it
# did NOT agree with the other one — THIS one carried `mode`, and
# `db_schema.QC_GATE_REGISTRY_DDL` did not. Both statements are
# `CREATE TABLE IF NOT EXISTS`, so a FRESH `ensure_schema` built the table from
# whichever ran, and the other was a SILENT no-op. `mode` is populated 9/9 on
# LIVE and read by this very module.
#
# The FIX has two halves and both are needed:
#   1. `db_schema.QC_GATE_REGISTRY_DDL` (the table's SSOT, and the statement a
#      FRESH build actually used) now DECLARES `mode` — see the note there;
#   2. THIS copy is gone. Leaving two statements that now agree would agree only
#      by luck, and the next edit to either side re-creates the drift. ONE
#      DECLARER PER TABLE.
#
# The NAME stays resolvable because importers read it. An empty string is a
# declaration that makes nothing, which is what this module now is for this
# table — a reader of the register, never its author.
QC_GATE_REGISTRY_DDL = ""


class GateError(ValueError):
    """A gate declaration that cannot be made, with a named reason."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure `qc_gate_registry` exists and carries `mode`. Idempotent.

    🔴 THE TABLE IS NOW DECLARED BY `db_schema.QC_GATE_REGISTRY_DDL`, NOT HERE
    (RING 5, D1, 2026-09-29). MEASURED: this module's copy did not agree with
    `db_schema`'s — only one of them had `mode` — and because both are
    `CREATE TABLE IF NOT EXISTS`, a FRESH build got whichever ran and the other
    was a SILENT no-op. Two declarations of one table, disagreeing, is the
    disease. So the SSOT (`db_schema`) declares it, and this module ENSURES it is
    there by asking `db_schema` for the statement rather than keeping a copy.

    WHY THIS DOES NOT JUST CALL `db_schema`: `qc_gate` is imported by tools that
    must not import the whole schema module (and `db_schema` imports plenty). So
    the table is created from the SSOT's OWN text when it is reachable, and the
    additive `mode` ALTER stays unconditionally — it is the half that repairs a
    LEGACY table, which no DDL text can do.
    """
    try:
        import db_schema as _ds
        conn.executescript(_ds.QC_GATE_REGISTRY_DDL)
    except Exception:
        # Best-effort, exactly like `db_schema`'s own lazy owner calls: a caller
        # whose environment cannot import `db_schema` still gets the ADDITIVE
        # repair below, and a table that already exists is not affected.
        pass
    have = {r[1] for r in conn.execute("PRAGMA table_info(qc_gate_registry)")}
    if "mode" not in have:
        try:
            conn.execute("ALTER TABLE qc_gate_registry ADD COLUMN mode TEXT "
                         "NOT NULL DEFAULT 'declare'")
        except Exception:
            pass


def _metric_unit_ok(kind: str, unit: str) -> bool:
    """`"<kind> of <subject>"` — the unit must NAME the kind it is a count of."""
    u = str(unit or "").strip().lower()
    return u.startswith(str(kind).strip().lower() + " of ")


def seed_gates(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write the nine declarations. Idempotent (UPSERT by `gate_key`).

    REFUSES rather than guessing: a gate whose `metric_kind` is outside the
    closed set, or whose `metric_unit` does not name its kind, is not written —
    a number that cannot be audited is a decoration.
    """
    ensure_schema(conn)
    created, updated = 0, 0
    for g in GATES:
        key = g["gate_key"]
        if g["metric_kind"] not in METRIC_KINDS:
            raise GateError(
                "gate %r metric_kind %r is not one of %s"
                % (key, g["metric_kind"], METRIC_KINDS))
        if not _metric_unit_ok(g["metric_kind"], g["metric_unit"]):
            raise GateError(
                "gate %r metric_unit %r does not name its kind %r "
                "(expected '%s of <subject>')"
                % (key, g["metric_unit"], g["metric_kind"], g["metric_kind"]))
        if g.get("polarity") not in POLARITIES:
            raise GateError(
                "gate %r polarity %r is not one of %s"
                % (key, g.get("polarity"), POLARITIES))
        if g.get("mode", "declare") not in MEASURE_MODES:
            raise GateError(
                "gate %r mode %r is not one of %s"
                % (key, g.get("mode"), MEASURE_MODES))
        if not str(g["cite_ref"] or "").strip():
            raise GateError("gate %r has no cite_ref — no citation, no gate" % key)
        row = conn.execute(
            "SELECT gate_id FROM qc_gate_registry WHERE gate_key=?", (key,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE qc_gate_registry SET gate_name=?, dimension=?, "
                "checker_ref=?, metric_kind=?, metric_unit=?, metric_target=?, "
                "polarity=?, mode=?, cite_ref=?, sort_order=?, "
                "updated_at=datetime('now') WHERE gate_id=?",
                (g["gate_name"], g["dimension"], g["checker_ref"],
                 g["metric_kind"], g["metric_unit"], float(g["metric_target"]),
                 g["polarity"], g.get("mode", "declare"), g["cite_ref"],
                 int(g["sort_order"]), int(row["gate_id"])),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO qc_gate_registry (gate_key, gate_name, dimension, "
                "checker_ref, metric_kind, metric_unit, metric_target, polarity, "
                "mode, cite_ref, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (key, g["gate_name"], g["dimension"], g["checker_ref"],
                 g["metric_kind"], g["metric_unit"], float(g["metric_target"]),
                 g["polarity"], g.get("mode", "declare"), g["cite_ref"],
                 int(g["sort_order"])),
            )
            created += 1
    conn.commit()
    return {"ok": True, "created": created, "updated": updated,
            "total": len(GATES)}


def list_gates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The nine gate declarations, in `sort_order`."""
    try:
        rows = conn.execute(
            "SELECT * FROM qc_gate_registry WHERE is_active=1 "
            "ORDER BY sort_order").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def gate_for(conn: sqlite3.Connection, gate_key: str) -> dict[str, Any] | None:
    """One gate declaration, or `None`. Never raises."""
    try:
        row = conn.execute(
            "SELECT * FROM qc_gate_registry WHERE gate_key=?",
            (str(gate_key),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def check_consistency(conn: sqlite3.Connection) -> dict[str, Any]:
    """Is the registry the SAME nine the tuple declares? Never raises.

    A registry that drifted from the tuple would let a gate run under a name the
    code does not know, or a declared gate silently go missing.
    """
    try:
        rows = list_gates(conn)
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    have = [r["gate_key"] for r in rows]
    missing = [k for k in GATE_KEYS if k not in have]
    extra = [k for k in have if k not in GATE_KEYS]
    return {"ok": not missing and not extra,
            "tuple_count": len(GATE_KEYS), "registry_count": len(have),
            "missing": missing, "extra": extra}


def main() -> int:
    ap = argparse.ArgumentParser(description="qc_gate declaration registry")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--seed", action="store_true", help="write the nine rows")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_gates(conn), indent=1))
        if args.list:
            rows = list_gates(conn)
            if not rows:
                print("(no qc_gate rows — run --seed)")
            for r in rows:
                print("  %d %-16s kind=%-12s target=%-6s cites=%s"
                      % (r["sort_order"], r["gate_key"], r["metric_kind"],
                         r["metric_target"], r["cite_ref"]))
        if args.check:
            print(json.dumps(check_consistency(conn), indent=1))
        if not (args.seed or args.list or args.check):
            print(json.dumps(check_consistency(conn), indent=1))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())