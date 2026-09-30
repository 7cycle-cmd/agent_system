# -*- coding: utf-8 -*-
"""measure_skill.py — record MEASURED proofs for a skill, from commands that ran.

THE RULE THIS ENFORCES
----------------------
A proof value must come from a MEASUREMENT, not from a person typing a number.
So this module does not accept a value: it accepts a COMMAND, RUNS it, and reads
the number out of the output. A value that cannot be produced by a command is not
recorded.

WHY THAT MATTERS HERE
---------------------
`skill_factor_proof.metric_value` is a number, and a number is trivially
fabricated. The only thing that makes it a measurement is that it can be
REPRODUCED. So the evidence_ref is a command, and the value is parsed from that
command's output — the same discipline `citation_discipline` applies to a
finding: no citation, no finding.

WHAT IT REFUSES
---------------
  * a factor with no `metric_unit` — a number without a unit cannot be audited
  * a command that FAILS — a failed command produced no measurement
  * a command whose output contains no number for the factor — an absent
    measurement is not a passing one
  * a value that does not match the factor's unit kind

THE FIRST SKILL MEASURED: `no_null_standard`
--------------------------------------------
Chosen because it is new (no history to reinterpret) and because its evidence
ALREADY EXISTS as commands that ran today:
    python _proof_no_null.py            35/0
    python skill_tdd_runner.py SKILL.NO.NULL.STANDARD   8/8
    python no_null.py --audit           the defect/allowed split
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import factor_first_principle as fp  # noqa: E402
import skill_factor as sf  # noqa: E402

DB = BASE / "agent.db"
PY = str(BASE / ".venv" / "Scripts" / "python.exe")


class NotMeasured(RuntimeError):
    """Raised when a value cannot be produced by a command."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("not measured — refusing to record: %s"
                         % "; ".join(reasons))


def run_command(cmd: str, *, timeout: int = 600) -> dict[str, Any]:
    """Run a command and return its output. A FAILED command is not a measurement.

    DEFECT FOUND BY RUNNING IT: the first version used `cmd.split()`, which breaks
    `python -c "print('x')"` into `['python','-c','"print(\'x\')"']` — the quotes
    survive and the interpreter sees a syntax error. A command is a SHELL string,
    so it is parsed with `shlex` (POSIX rules, which match how the commands in
    this repo are written) and the venv python is substituted for `python`.
    """
    import shlex
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError as e:
        return {"ok": False, "output": "", "returncode": -1,
                "error": "unparseable command: %s" % e}
    if parts and parts[0] in ("python", "py"):
        parts[0] = PY
    try:
        p = subprocess.run(parts, cwd=str(BASE), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except Exception as e:
        return {"ok": False, "output": "", "returncode": -1,
                "error": "%s: %s" % (type(e).__name__, e)}
    out = (p.stdout or "") + (p.stderr or "")
    return {"ok": p.returncode == 0, "output": out,
            "returncode": p.returncode, "error": ""}


def parse_metric(output: str, pattern: str) -> str | None:
    """Read the number out of a command's output using a named pattern.

    The pattern is a REGEX with one capture group. It is supplied per factor, so
    the extraction is explicit rather than a guess at "the first number".
    """
    m = re.search(pattern, output, re.M)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# THE MEASUREMENT PLAN for `no_null_standard`.
#
# Each entry names: the factor, the COMMAND, and the REGEX that reads the value
# out of that command's output. Nothing here is a typed number.
#
# DEFECT FOUND BY THE UNIT, AND IT WAS MINE: the first plan fed a COUNT into
# factors whose unit says `pct`. Measured: `tdd_unit_test_eval` got 8 (cases
# passed) against a target of 100 on a 0-100 scale, and `environment_registry_
# check` got 35 (assertions) against a target of 100 percent. Both FAILED, and
# they were RIGHT to fail — the number did not match the unit. That is the whole
# point of requiring a unit: it caught a measurement that was measuring the wrong
# thing. The patterns below now compute the RATIO the unit asks for.
# ---------------------------------------------------------------------------
PLAN: dict[str, list[dict[str, str]]] = {
    "no_null_standard": [
        dict(factor_key="tdd_unit_test_eval",
             cmd="python skill_tdd_runner.py SKILL.NO.NULL.STANDARD",
             pattern=r"(\d+)/(\d+) cases passed",
             ratio=True, ratio_mode="part_of_total",
             note="unit is `score_0_100 of test suite stability`, so the value "
                  "is the PASS RATE as a percentage, not the case count"),
        dict(factor_key="environment_registry_check",
             cmd="python _proof_no_null.py",
             pattern=r"no_null proof: (\d+) passed / (\d+) failed",
             ratio=True, ratio_mode="part_of_sum",
             note="unit is `pct of required dependencies ready`, so the value is "
                  "passed/(passed+failed) as a percentage"),
        dict(factor_key="ntd_no_bypass_registry_ontology",
             cmd="python _proof_factor_first_principle.py",
             pattern=r"factor_first_principle proof: \d+ passed / (\d+) failed",
             note="unit is `count of register bypass attempts`; the FAILED count "
                  "of the register audit"),
        dict(factor_key="template_source_ontology_binding",
             cmd="python factor_first_principle.py --audit",
             pattern=r'"measurable": (\d+),\s*\n\s*"not_measurable": (\d+)',
             ratio=True, ratio_mode="part_of_sum",
             note="unit is `pct of entities matching the Ontology definition`, so "
                  "the value is measurable/(measurable+not_measurable)"),

        # ------------------------------------------------------------------
        # THE REMAINING 7. Each entry is a CANDIDATE: the command was found by
        # READING the repo, not by running it. A candidate that prints no number
        # for its factor is reported NO_NUMBER_FOUND — a real outcome, not a
        # failure to hide. Nothing here is a typed value.
        # ------------------------------------------------------------------
        dict(factor_key="field_meta_ontology_property",
             cmd="python _proof_p1_coverage.py",
             pattern=r"contracts meeting .*: (\d+)/(\d+)",
             ratio=True, ratio_mode="part_of_total",
             note="unit is `pct of fields mapped to an Ontology property`; the "
                  "P1 proof measures how many contracts have their Field Register "
                  "actually bound (>=5 fields / >=3 pass / >=2 hard_fail)"),
        dict(factor_key="lazy_fk_ontology_relation",
             cmd="python null_columns.py --classify",
             pattern=r'"REFERENCE_BY_NAME": (\d+)',
             note="unit is `count of illegal NULL references accepted`; a column "
                  "whose NAME promises a reference (_id/_ref/_key/_hash) and holds "
                  "NULL is an ILLEGAL reference — the name says it points at "
                  "something and it points at nothing. That is the "
                  "REFERENCE_BY_NAME category count, NOT the defect_nulls total "
                  "(which also counts VALUE columns that are merely undecided)"),
        dict(factor_key="schema_snapshot_rollback",
             cmd="python run_ontology_revision_tests.py",
             pattern=r'"pass_count": (\d+),\s*\n\s*"fail_count": (\d+)',
             ratio=True, ratio_mode="part_of_sum",
             note="unit is `pct of rollback restores succeeding`; tc.2.* includes "
                  "rollback_to_prior_revision, so the pass rate over the revision "
                  "suite is the restore success rate"),
        dict(factor_key="ntd_no_direct_production_execute",
             cmd="python _proof_p0_gates.py",
             pattern=r"LEAKED!",
             count_occurrences=True,
             positive_control=r"BLOCKED",
             note="unit is `count of direct production write attempts`; the P0 "
                  "proof writes through the REAL path and prints LEAKED! when a "
                  "non-owner write got through, so the count of LEAKED! is the "
                  "count of bypasses that succeeded. BLOCKED is the positive "
                  "control: without it, 0 LEAKED! cannot be told apart from a "
                  "proof that never ran"),
        dict(factor_key="ntd_no_auto_purge_legal_null",
             cmd="python _proof_null_backfill.py",
             pattern=r"null backfill proof: \d+ passed / (\d+) failed",
             note="unit is `count of legal NULL rows deleted`; the backfill proof "
                  "asserts that only NULLs are touched and a real value is NEVER "
                  "rewritten, so its FAILED count is the count of legal values "
                  "destroyed"),

        # ------------------------------------------------------------------
        # NO COMMAND EXISTS. These two are reported, not omitted: a factor with
        # no command that produces its number is UNMEASURED, and saying so is
        # the honest result. Filling them with a typed value is the exact defect
        # this module exists to prevent.
        # ------------------------------------------------------------------
        dict(factor_key="table_meta_scoring_link",
             cmd=None,
             note="unit is `count of missing required metadata fields`. NO COMMAND "
                  "produces this number today: `code_health.py verify-schema` "
                  "prints a boolean `ok` per table, not a count of missing "
                  "metadata fields. A boolean is not a count."),
        dict(factor_key="qc_review_scoring_approval",
             cmd=None,
             note="unit is `score_0_100 of total quality`. NO COMMAND produces "
                  "this number today: `code_health.py report` prints a "
                  "per-function `functional_score` that is null for every "
                  "function, and no command combines ontology + test + security "
                  "into one total. A null is not a score."),

        # ------------------------------------------------------------------
        # ADDED 2026-09-22. `phone_validity_judgment` is a GENERIC factor
        # (`applies_to='*'`), so it applies to EVERY skill — including this one.
        # The plan did not cover it, and `plan_coverage` reported the gap
        # (11 planned / 12 applicable). That is the coverage check WORKING.
        #
        # The command IS the 100-run harness, and the value is the WIN RATE the
        # unit asks for (`pct of cases judged correctly`), not the round count.
        # ------------------------------------------------------------------
        dict(factor_key="phone_validity_judgment",
             cmd="python phone_100_proof_runner.py --report",
             pattern=r"wins=(\d+)\s+rounds=(\d+)",
             ratio=True, ratio_mode="part_of_total",
             note="unit is `pct of cases judged correctly`, so the value is the "
                  "WIN RATE as a percentage, not the round count. A candidate: "
                  "the command was found by READING the repo, and a command that "
                  "prints no number for its factor is reported NO_NUMBER_FOUND."),
    ],

    # ------------------------------------------------------------------
    # THE 3 CODING SKILLS (STRUCTURE.SKILL.PROOFED.ACTIVE, WP3, 2026-09-27).
    #
    # Each entry covers ONE applicable factor. The OWN factors (the ones the
    # skill declares) have NO command that produces their number today — the
    # proofs they name measure OTHER things, not these factors. So they are
    # `cmd=None` -> `NO_COMMAND`: a factor with no command is UNMEASURED, and
    # saying so is the honest result. Filling them with a typed value is the
    # exact defect this module exists to prevent.
    #
    # The GENERIC factors (`applies_to='*'`) apply to every skill, so they are
    # listed too — otherwise `plan_coverage` reports them as `missing`. They
    # are also `cmd=None`: no command produces their number FOR THESE SKILLS.
    # ------------------------------------------------------------------
    "skill_worker_code_builder": [
        dict(factor_key="declared_before_used", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="attributed_to_entity", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="claim_carries_cite", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="precondition_constructed", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="one_parser_only", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
    ] + [
        dict(factor_key=fk, cmd=None,
             note="generic factor; NO COMMAND for this skill today.")
        for fk in (
            "capability_declaration_not_observation", "capability_has_kind",
            "capability_tool_is_declared", "discovery_kind_matches_registry",
            "environment_registry_check", "field_meta_ontology_property",
            "lazy_fk_ontology_relation", "lesson_has_cite", "lesson_is_filed",
            "ntd_no_auto_purge_legal_null", "ntd_no_bypass_registry_ontology",
            "ntd_no_direct_production_execute", "ntd_no_hardcoded_secrets",
            "phone_validity_judgment", "probe_side_effect_free",
            "qc_review_scoring_approval", "runtime_liveness_evidence",
            "schema_snapshot_rollback", "security_sast_gate",
            "soft_delete_has_cite", "soft_delete_no_orphan",
            "table_meta_scoring_link", "tag_definition_has_cite",
            "tag_not_duplicate", "tdd_unit_test_eval",
            "template_source_ontology_binding",
        )
    ],
    "skill_task_done_verification": [
        dict(factor_key="verdict_is_read", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="relation_not_count", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="prose_is_not_code", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="control_present", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="green_is_measured", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
    ] + [
        dict(factor_key=fk, cmd=None,
             note="generic factor; NO COMMAND for this skill today.")
        for fk in (
            "discovery_kind_matches_registry", "environment_registry_check",
            "field_meta_ontology_property", "lazy_fk_ontology_relation",
            "ntd_no_auto_purge_legal_null", "ntd_no_bypass_registry_ontology",
            "ntd_no_direct_production_execute", "phone_validity_judgment",
            "qc_review_scoring_approval", "runtime_liveness_evidence",
            "schema_snapshot_rollback", "table_meta_scoring_link",
            "tdd_unit_test_eval", "template_source_ontology_binding",
        )
    ],
    "skill_api_standard_validator": [
        dict(factor_key="api_spec_present", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="result_is_yes_no", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
        dict(factor_key="check_only_no_write", cmd=None,
             note="NO COMMAND produces this number for this skill today."),
    ] + [
        dict(factor_key=fk, cmd=None,
             note="generic factor; NO COMMAND for this skill today.")
        for fk in (
            "discovery_kind_matches_registry", "environment_registry_check",
            "field_meta_ontology_property", "lazy_fk_ontology_relation",
            "ntd_no_auto_purge_legal_null", "ntd_no_bypass_registry_ontology",
            "ntd_no_direct_production_execute", "phone_validity_judgment",
            "qc_review_scoring_approval", "runtime_liveness_evidence",
            "schema_snapshot_rollback", "table_meta_scoring_link",
            "tdd_unit_test_eval", "template_source_ontology_binding",
        )
    ],
}


# A GENERIC factor (`applies_to='*'`) applies to EVERY skill, and it is added by
# a DIFFERENT round than the one that writes a PLAN. So a hand-written PLAN
# cannot enumerate them without drifting the moment the next generic factor is
# registered. MEASURED 2026-09-29: `no_null_standard`'s plan held 12 entries
# while 24 factors applied, and the 12 missing were ALL generic (`laya_*`,
# `ui_*`, `verdict_requires_proof_not_agreement`, ...) added by other rounds.
# The drift was real, but the REPAIR is not 12 hand-typed lines — that recreates
# the same coupling one round later. The mechanism fills them instead.
AUTO_NO_COMMAND_NOTE = (
    "generic factor (applies_to='*'), auto-added by measure_skill: it applies to "
    "this skill but the PLAN declares no command that produces its number HERE. "
    "Reported NO_COMMAND rather than omitted, so the gap is visible.")


def effective_plan(conn: sqlite3.Connection, skill_key: str) -> list[dict]:
    """The PLAN steps PLUS a `NO_COMMAND` step for every applicable factor the
    PLAN does not mention. DECLARED steps come first, so a factor that HAS a
    command is never shadowed by the auto-added entry.

    This is the fix for the drift: the applicable set is DB-derived, so it grows
    without a human editing four hand-written plans. A factor with no command is
    still REPORTED (`NO_COMMAND`), never quietly dropped — the honest state.
    """
    declared = list(PLAN.get(skill_key, []))
    have = {s["factor_key"] for s in declared}
    extra = [dict(factor_key=f["factor_key"], cmd=None, auto=True,
                  note=AUTO_NO_COMMAND_NOTE)
             for f in sf.applicable_factors(conn, skill_key)
             if f["factor_key"] not in have]
    return declared + extra


def plan_coverage(conn: sqlite3.Connection, skill_key: str) -> dict[str, Any]:
    """Does the plan cover EXACTLY the applicable factors?

    DEFECT FOUND BY MEASURING IT: the plan held 10 entries while 11 factors
    applied. Two entries were for factors that do NOT apply (`dry_run_before_apply`
    is crud-only, `ntd_no_hardcoded_secrets` is code-only) and three applicable
    factors were missing. A plan that is not compared against the applicable set
    drifts in both directions at once, and the drift is invisible because the
    run simply reports fewer rows.

    MEASURED AGAIN 2026-09-29: the coverage is now computed against the
    EFFECTIVE plan (declared + auto-added NO_COMMAND entries), so a generic
    factor added by a later round is covered WITHOUT editing this file. The
    DECLARED count is reported separately, so "how many factors have a real
    command" stays visible and a plan that stops declaring commands is caught.
    """
    applicable = {f["factor_key"] for f in sf.applicable_factors(conn, skill_key)}
    effective = {s["factor_key"] for s in effective_plan(conn, skill_key)}
    declared = {s["factor_key"] for s in PLAN.get(skill_key, [])}
    return {"applicable": len(applicable), "planned": len(effective),
            "declared": len(declared),
            "with_command": sum(1 for s in PLAN.get(skill_key, []) if s.get("cmd")),
            "auto_filled": len(effective - declared),
            "missing": sorted(applicable - effective),
            "not_applicable": sorted(declared - applicable),
            "complete": applicable == effective}


def measure_skill(conn: sqlite3.Connection, skill_key: str, *,
                  apply: bool = False) -> dict[str, Any]:
    """Run the plan for a skill and record what the commands produced."""
    plan = effective_plan(conn, skill_key)
    if not plan:
        raise NotMeasured(["no measurement plan for skill %r" % skill_key])
    cov = plan_coverage(conn, skill_key)
    results: list[dict[str, Any]] = []
    for step in plan:
        fk = step["factor_key"]
        f = conn.execute("SELECT * FROM skill_factor_registry WHERE factor_key=?",
                         (fk,)).fetchone()
        if not f:
            results.append({"factor_key": fk, "state": "NO_SUCH_FACTOR"})
            continue
        # A factor with no unit cannot be audited, so it is not measured.
        verdict = fp.audit_factor(dict(f))
        if not verdict["measurable"]:
            results.append({"factor_key": fk, "state": "NOT_MEASURABLE",
                            "reason": verdict["reasons"][0][:70]})
            continue
        if not step.get("cmd"):
            # NO COMMAND EXISTS for this factor. Reported as its own state, so a
            # reader sees a factor that is UNMEASURED and WHY — not a factor that
            # quietly vanished from the plan.
            results.append({"factor_key": fk, "state": "NO_COMMAND",
                            "reason": step.get("note", "")[:120]})
            continue
        r = run_command(step["cmd"])
        if not r["ok"]:
            results.append({"factor_key": fk, "state": "COMMAND_FAILED",
                            "cmd": step["cmd"],
                            "returncode": r["returncode"],
                            "reason": "a failed command produced no measurement"})
            continue
        if step.get("positive_control") and not re.search(
                step["positive_control"], r["output"], re.M):
            # independent-review: an EMPTY result is refused unless a positive
            # control proves the detector can find something. `LEAKED!` never
            # appearing is only a measurement if `BLOCKED` DID appear — otherwise
            # the check did not run, and 0 is indistinguishable from "no check".
            results.append({"factor_key": fk, "state": "NO_POSITIVE_CONTROL",
                            "cmd": step["cmd"],
                            "reason": "the detector printed no positive control "
                                      "(%s), so a zero count cannot be told apart "
                                      "from a check that never ran"
                                      % step["positive_control"]})
            continue
        m = re.search(step["pattern"], r["output"], re.M)
        if not m and not step.get("count_occurrences"):
            results.append({"factor_key": fk, "state": "NO_NUMBER_FOUND",
                            "cmd": step["cmd"],
                            "reason": "the output contained no value for this "
                                      "factor; an absent measurement is not a "
                                      "passing one"})
            continue
        if step.get("count_occurrences"):
            # A COUNT OF EVENTS: the command prints one marker per event, so the
            # count of markers IS the measurement. Zero is a real value here —
            # which is exactly why a positive control is required above.
            value = str(len(re.findall(step["pattern"], r["output"], re.M)))
        elif step.get("ratio"):
            # The unit asks for a RATIO, so the value is computed from the two
            # captured counts. A ratio whose denominator is 0 is REFUSED rather
            # than reported as 0 or 100 — an undefined ratio is not a pass.
            #
            # DEFECT FOUND BY RUNNING IT: the first version ADDED the two
            # captures, so `8/8 cases passed` became 8/(8+8) = 50.0 and the
            # factor FAILED while the suite was perfect. The two captures are
            # not always (part, part) — sometimes they are (part, TOTAL). So the
            # mode is explicit, and a wrong mode is a visible choice rather than
            # a silent arithmetic assumption.
            try:
                a, b = int(m.group(1)), int(m.group(2))
            except (IndexError, ValueError):
                results.append({"factor_key": fk, "state": "RATIO_UNREADABLE",
                                "cmd": step["cmd"],
                                "reason": "the pattern did not capture two counts"})
                continue
            mode = step.get("ratio_mode", "part_of_sum")
            if mode == "part_of_total":
                num, den = a, b
            elif mode == "part_of_sum":
                num, den = a, a + b
            else:
                results.append({"factor_key": fk, "state": "RATIO_MODE_UNKNOWN",
                                "cmd": step["cmd"],
                                "reason": "ratio_mode %r is not one of "
                                          "part_of_total / part_of_sum" % mode})
                continue
            if den == 0:
                results.append({"factor_key": fk, "state": "RATIO_UNDEFINED",
                                "cmd": step["cmd"],
                                "reason": "0 of 0 is undefined, not 100%%"})
                continue
            value = "%.1f" % (100.0 * num / den)
        else:
            grp = int(step.get("pattern_group", 1))
            value = m.group(grp).strip()
        # THE SCOPE. The READER is the command that produced the number; the
        # POPULATION is the factor's own row (a proof measures a factor), which
        # is the row `f` fetched above. `record_proof` REFUSES a proof with no
        # scope, so this is built from data already in hand — no new measurement.
        scope = sf.proof_scope(
            conn, fk, reader=step["cmd"], reader_cite=step["cmd"],
            count_command=step["cmd"], population_count=1,
            population="skill_factor_registry WHERE factor_key=%s" % fk,
            population_cite="measure_skill.py:measure_skill")
        rec = sf.record_proof(conn, skill_key, fk, metric_value=value,
                              evidence_ref=step["cmd"], scope=scope) if apply else {
            "skill_key": skill_key, "factor_key": fk, "metric_value": value,
            "metric_pass": None, "proof_artifact_id":
                sf.proof_artifact_id(skill_key, fk)}
        results.append({"factor_key": fk, "state": "MEASURED",
                        "cmd": step["cmd"], "value": value,
                        "metric_pass": rec.get("metric_pass"),
                        "proof_artifact_id": rec["proof_artifact_id"],
                        "note": step["note"]})
    return {"skill_key": skill_key, "applied": apply,
            "coverage": cov,
            "measured": sum(1 for r in results if r["state"] == "MEASURED"),
            "refused": sum(1 for r in results if r["state"] != "MEASURED"),
            "results": results}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--skill", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        out = measure_skill(conn, args.skill, apply=args.apply)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()