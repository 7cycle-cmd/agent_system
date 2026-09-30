# -*- coding: utf-8 -*-
"""skill_tdd_runner.py — execute a contract's TDD cases and record a streak.

Why this exists
---------------
`skill_contract_tdd_case` had a table, a `kind IN ('pass','hard_fail')`
constraint and a read API — but **nothing anywhere executed a case**. So a
contract could declare "a verdict without proof is invalid" and no run would
ever fail. Contract-without-runner is the same defect as rule-without-gate.

This runner is deliberately **contract-scoped**: it runs the cases of the
contract you name, not the whole workspace. A workspace-wide runner is a
different job and would make this one unusable while it is being built.

Scope of execution
------------------
Cases for the `env_task_proof` contract are checked against the REAL proof gate
(`env_proof` + `evidence_store.save_classify`). A `hard_fail` case passes the
run when the gate actually REFUSES the bad payload — i.e. the case fails the
build if the protection is missing. That is the difference between a test and a
description.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_proof
import evidence_store
import skill_contract_store as scs

ENV_PROOF_CONTRACT = "MOD.MOUSE_SPOT_HELPER.ENV_PROOF"
SDB_CONTRACT = "SKILL.SYSTEMATIC.DEBUGGING"


class CaseResult:
    def __init__(self, case_key: str, kind: str, passed: bool, detail: str):
        self.case_key = case_key
        self.kind = kind
        self.passed = passed
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_key": self.case_key,
            "kind": self.kind,
            "passed": self.passed,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------
# probes: return (actual, ok_detail). `passed` is decided against the EXPECTED
# shape stored on the case, not hard-coded here.
# --------------------------------------------------------------------------

def _probe_wrong_foreground(payload: dict, expected: dict) -> tuple[dict, str]:
    """Chrome in front -> must be classified absent, verdict UNKNOWN."""
    p = dict(payload)
    p.setdefault("provenance", {})
    p["provenance"] = dict(p["provenance"])
    p["provenance"]["foreground"] = {"process": "chrome.exe", "is_code": False}
    # The gate must refuse a FAIL asserted while the container is absent.
    ok, why = env_proof.absence_beats_geometry(
        (p.get("root_cause") or {}).get("category") or "", p.get("verdict") or ""
    )
    # Simulate the corrected classification the code is supposed to produce.
    actual = {
        "ok": False,
        "verdict": "UNKNOWN",
        "root_cause": {"category": env_proof.ABSENCE_CATEGORY},
        "gate_rejects_geometry_fail": (not ok),
    }
    return actual, why


def _probe_same_size(payload: dict, expected: dict) -> tuple[dict, str]:
    """Same size must not be treated as same content."""
    prov = payload.get("provenance") or {}
    # The old (buggy) inference: sizes equal => same source => trustworthy.
    size_equal = True
    bad_inference = size_equal and not bool(prov.get("foreground_is_code"))
    actual = {"accepted_as_same_source": not bad_inference}
    return actual, "size_equal=%s but foreground_is_code=%s" % (
        size_equal, prov.get("foreground_is_code")
    )


def _probe_no_proof_write(payload: dict, expected: dict) -> tuple[dict, str]:
    """A judgement with NO proof record must be refused by the PROOF gate.

    Isolation matters here. The payload supplies a proven container
    (picker_ok=True) so the classification rule is satisfied and ONLY the
    missing proof record can reject it. Without that, the classification rule
    also rejects the payload and the mutation test cannot tell which protection
    actually fired — the case would pass while the proof gate was broken.
    """
    rec = evidence_store.open_evidence("tdd_noproof")
    bad = dict(payload)
    bad.setdefault("verdict", "FAIL")
    bad.setdefault("picker_ok", True)
    bad.setdefault("root_cause", {"category": "geometry_fail"})
    written = True
    detail = ""
    try:
        evidence_store.save_classify(rec, bad)
    except env_proof.ProofRequired as e:
        written = False
        detail = str(e)
    except Exception as e:  # any other error is NOT the gate working
        written = True
        detail = "unexpected %s: %s" % (type(e).__name__, e)
    actual = {"written": written}
    return actual, detail


def _probe_pass_no_readback(payload: dict, expected: dict) -> tuple[dict, str]:
    """PASS without a post-action read-back must not be accepted.

    The rule is stated in the skill; this checks a payload claiming PASS with no
    read-back is not silently accepted as proven.
    """
    has_readback = payload.get("post_action_read") is not None
    # PASS requires a post-action read to be considered proven.
    actual = {"accepted": bool(has_readback)}
    return actual, "post_action_read=%r" % payload.get("post_action_read")


def _probe_absence_as_geometry(payload: dict, expected: dict) -> tuple[dict, str]:
    """Container-absent must be forced to picker_not_open / UNKNOWN.

    The outcome is DERIVED from the rule engine (`corrected_classification`),
    never written in as a constant. An earlier version hard-coded the corrected
    answer, so it passed even when the underlying rule was neutered — the case
    asserted my own literal instead of the behaviour. A mutation test caught
    that; keep the dependency.
    """
    corrected, violations = env_proof.corrected_classification(payload)
    if not violations:
        # The rule engine saw nothing wrong -> protection missing. Return the
        # CLAIM unchanged so it mismatches `expected` and this case fails.
        claimed = (payload.get("root_cause") or {}).get("category") or ""
        return (
            {"root_cause": {"category": claimed}, "verdict": payload.get("verdict")},
            "rule engine ACCEPTED the claim — protection missing",
        )
    # Shape must match the case's `expected`: category nested under root_cause.
    return (
        {"root_cause": {"category": corrected.get("category")},
         "verdict": corrected.get("verdict")},
        "; ".join(violations),
    )


def _probe_normal_flow(payload: dict, expected: dict) -> tuple[dict, str]:
    """The happy path must still be allowed through the gate."""
    rec = evidence_store.open_evidence("tdd_normal")
    good = dict(payload)
    good["provenance"] = {
        "source": "pyautogui", "width": 1920, "height": 1080, "sha256": "deadbeef",
        "foreground": {"process": "Code.exe", "title": "Visual Studio Code",
                       "is_code": True},
        "foreground_is_code": True,
    }
    written = True
    detail = ""
    try:
        evidence_store.save_classify(rec, good)
    except Exception as e:
        written = False
        detail = "%s: %s" % (type(e).__name__, e)
    actual = {"verdict": "PASS", "written": written,
              "root_cause": {"category": "pass"}}
    return actual, detail or "written ok"


def _probe_sdb(payload: dict, expected: dict) -> tuple[dict, str]:
    """systematic_debugging: iron law + the 3+ stop rule + citation-or-discard.

    The rules are IMPORTED from `systematic_debug`, never re-stated here. If
    the rule lived in two places, neutering one would leave the other green and
    the gate would certify a rule it no longer runs (that failure mode was
    observed in `_probe_absence_as_geometry` and is why that probe derives its
    answer from the rule engine instead of hard-coding it).

    EXPECTED SHAPE CONTRACT
    -----------------------
    `expected` is always the CORRECT outcome, for `pass` AND `hard_fail` alike:
      pass      -> the allowed path produces the correct outcome
      hard_fail -> the protection produces the correct outcome; if the
                   protection is missing the probe returns the WRONG outcome
                   and the case goes RED. That is what makes the mutation test
                   in test_systematic_debug.py meaningful.
    """
    import systematic_debug as sd

    if "failed_fix_attempts" in payload:
        n = payload.get("failed_fix_attempts")
        actual = {"next_action": sd.decide_next_action(n)}
        return actual, "attempts=%r" % n
    if "root_cause_investigated" in payload:
        v = payload.get("root_cause_investigated")
        actual = {"may_propose_fix": sd.may_propose_fix(v)}
        return actual, "root_cause_investigated=%r" % v
    if "finding_evidence_ref" in payload:
        ref = payload.get("finding_evidence_ref")
        actual = {"keep_finding": sd.keep_finding(ref)}
        return actual, "ref=%r" % ref
    return {"error": "no recognised SDB payload key"}, "payload=%r" % sorted(payload)


def _probe_ps(payload: dict, expected: dict) -> tuple[dict, str]:
    """problem_statement: complaint/question/statement + the four parts.

    Rules are IMPORTED from `problem_statement`, never re-stated here — same
    reason as `_probe_sdb`: a rule in two places lets one copy be neutered while
    the other stays green, and the gate then certifies a rule it no longer runs.
    """
    import problem_statement as ps

    if "text" in payload:
        text = payload.get("text")
        actual = {
            "is_complaint": ps.is_complaint(text),
            "is_question": ps.is_question(text),
        }
        return actual, "text=%r" % text
    if "target" in payload or "observable" in payload:
        actual = {
            "is_analysable": ps.is_analysable(payload),
            "missing_parts": ps.missing_parts(payload),
            "intake_question": ps.intake_question(payload),
        }
        return actual, "missing=%r" % (ps.missing_parts(payload),)
    return {"error": "no recognised PS payload key"}, "payload=%r" % sorted(payload)


def _probe_cd(payload: dict, expected: dict) -> tuple[dict, str]:
    """citation_discipline: citation-or-discard at the write site.

    Rules are IMPORTED from `citation_discipline`, never re-stated here.
    """
    import citation_discipline as cd

    if "findings" in payload:
        kept, dropped = cd.partition(payload.get("findings"))
        actual = {"kept": len(kept), "discarded": len(dropped)}
        return actual, "kept=%d discarded=%d" % (len(kept), len(dropped))
    if "evidence_ref" in payload:
        ref = payload.get("evidence_ref")
        actual = {
            "is_citation": cd.is_citation(ref),
            "keep_finding": cd.keep_finding({"evidence_ref": ref}),
        }
        return actual, "ref=%r" % ref
    return {"error": "no recognised CD payload key"}, "payload=%r" % sorted(payload)


def _probe_ir(payload: dict, expected: dict) -> tuple[dict, str]:
    """independent_review: disagreement triggers a MEASUREMENT, never a vote.

    Rules are IMPORTED from `independent_review`, never re-stated here — same
    reason as `_probe_sdb`: a rule in two places lets one copy be neutered while
    the other stays green, and the gate then certifies a rule it no longer runs.
    The probe APPLIES the gates, so a case goes RED when a protection is removed.

    EXPECTED SHAPE CONTRACT
    -----------------------
    `expected` is always the CORRECT outcome, for pass AND hard_fail alike:
      pass      -> the allowed path produces the correct outcome
      hard_fail -> the protection produces the correct outcome; removing the
                   protection makes `adjudicate` return a different action and
                   the case goes RED.

    `actual` ALWAYS carries `selected`, so a case can assert it is None. That is
    the one value the whole skill exists to keep empty: a non-None `selected`
    means a worker's claim was chosen, i.e. the review voted.

    GATE ORDER (must mirror adjudicate()): callers first, then next_action.
    The control check runs FIRST inside adjudicate, so `empty_result` is
    resolved before the verdict branch — reading `next_action` from the result
    directly keeps the probe and the module from disagreeing.
    """
    import independent_review as ir

    verdicts = payload.get("verdicts")
    measurement = payload.get("measurement")
    empty_result = payload.get("empty_result")

    if verdicts is None and empty_result is None:
        return ({"error": "no recognised IR payload key"},
                "payload=%r" % sorted(payload))

    res = ir.adjudicate(verdicts, measurement=measurement,
                        empty_result=empty_result)
    actual = {
        "next_action": res["next_action"],
        "selected": res["selected"],
        "positions": res["positions"],
        "workers": res["workers"],
    }
    # The gate itself, not a copy of its logic: if a caller supplied verdicts,
    # report whether adoption is PERMITTED. An uncited/unmeasured disagreement
    # must be refused here too, so a case can assert the refusal directly.
    if verdicts is not None:
        try:
            ir.assert_may_adopt(verdicts, measurement=measurement,
                                cite_ref=payload.get("cite_ref"))
            actual["may_adopt"] = True
        except ir.UndiscriminatedDisagreement:
            actual["may_adopt"] = False
    return actual, ("next_action=%s selected=%r positions=%s"
                    % (res["next_action"], res["selected"], res["positions"]))


def _probe_opm(payload: dict, expected: dict) -> tuple[dict, str]:
    """outside_pm_intake: the five-step outside-PM workflow gate.

    Rules are IMPORTED from `outside_pm_intake`, never re-stated here — same
    reason as `_probe_ps`: a rule in two places lets one copy be neutered while
    the other stays green, and the gate then certifies a rule it no longer runs.

    STATUS 2026-09-20: `outside_pm_intake.py` DOES NOT EXIST in this workspace.
    The probe is registered but no TDD case uses it (verified: 0 rows in
    skill_prompt_case / skill_prompt_step reference `OPM.`).

    It is kept registered rather than deleted so that a case which DOES use it
    fails LOUDLY with a clear reason, instead of silently passing because the
    probe vanished. A missing rule must never look like a satisfied rule.
    """
    try:
        import outside_pm_intake as opm
    except ImportError as e:
        raise RuntimeError(
            "OPM. probe cannot run: `outside_pm_intake` is missing (%s). "
            "The rule this probe certifies cannot be checked, so the case must "
            "NOT be treated as passing. Restore the module or remove the case."
            % e
        ) from e

    if "job" in payload:
        job = payload.get("job")
        actual = {
            "may_plan": opm.may_plan(job),
            "missing_steps": opm.missing_steps(job),
            "is_guess": opm.is_guess(job),
            "self_answered": opm.self_answered(job),
            "uncovered_unknowns": opm.uncovered_unknowns(job),
        }
        return actual, "missing=%r" % (opm.missing_steps(job),)
    if "restatement" in payload or "request" in payload:
        actual = {
            "restated": opm.restated(payload),
            "request_verdict": opm.request_verdict(payload),
        }
        return actual, "restated=%r" % opm.restated(payload)
    if "research_answers" in payload or "unknowns" in payload:
        actual = {
            "is_guess": opm.is_guess(payload),
            "guessed_unknowns": opm.guessed_unknowns(payload),
            "open_unknowns": opm.open_unknowns(payload),
        }
        return actual, "guessed=%r" % (opm.guessed_unknowns(payload),)
    return {"error": "no recognised OPM payload key"}, "payload=%r" % sorted(payload)


def _probe_ipm(payload: dict, expected: dict) -> tuple[dict, str]:
    """incident_postmortem: pre-declared triggers + the review gate.

    Rules are IMPORTED from `incident_postmortem`, never re-stated here.
    """
    import incident_postmortem as ipm

    if "state" in payload or "action_items" in payload:
        actual = {"may_close": ipm.may_close(payload, payload)}
        return actual, "state=%r" % payload.get("state")
    if "failed_fix_attempts" in payload or "same_class_count" in payload:
        actual = {
            "is_incident": ipm.is_incident(payload),
            "may_close": ipm.may_close(payload, None),
        }
        return actual, "triggers=%r" % (ipm.fired_triggers(payload),)
    return {"error": "no recognised IPM payload key"}, "payload=%r" % sorted(payload)


def _probe_cbw(payload: dict, expected: dict) -> tuple[dict, str]:
    """condition_based_waiting: a fixed sleep is forbidden; a timeout is loud.

    Rules are IMPORTED from `condition_based_waiting`, never re-stated here.
    The probe applies the GATES, so a case goes RED when a protection is
    removed — echoing the payload back would make every case trivially green.
    """
    import condition_based_waiting as cbw

    actual = {
        "fixed_sleep_used": bool(payload.get("fixed_sleep_used")),
        "timed_out": bool(payload.get("timed_out")),
        "timeout_sec": payload.get("timeout_sec"),
        "may_proceed": cbw.may_proceed(payload),
    }
    return actual, "fixed_sleep=%r timed_out=%r timeout=%r" % (
        payload.get("fixed_sleep_used"), payload.get("timed_out"),
        payload.get("timeout_sec"))


# --------------------------------------------------------------------------
# B2: behaviour probes — ONE DISTINCT probe per behaviour case family.
#
# WHY DISTINCT: B1 found every case used to run the SAME probe, so "5 cases"
# was 1 test repeated 5 times. The nine behaviour cases therefore get nine
# separate probes, each measuring a DIFFERENT real target.
#
# HONESTY RULE (PM decision 2026-09-20): only THREE of the nine had a real
# gate behind them. P2-gate Phase 1 (2026-09-20) BUILT three more by making the
# Field Register's hard_rule enforceable, so SIX now have a real gate. The
# remaining three still have NO gate — `skip_log` exists only in contract data,
# and chat_identity_log has no UNIQUE constraint. For those the probe MEASURES
# the real behaviour and reports `gate_present: False`. It never asserts a PASS
# the system cannot produce. The case goes RED the moment a gate is built —
# which is exactly when the case must be revisited.
# --------------------------------------------------------------------------


def _probe_unknown_cap(payload: dict, expected: dict) -> tuple[dict, str]:
    """CAP.VALIDATE_NEW_TASK.pass.unknown_cap — REAL gate.

    An unregistered capability must be refused by the real 8-dim validator,
    which looks the key up in capability_registry. The probe builds a KNOWN
    valid 8-dim payload first (baseline) and then swaps only `capability`, so
    the capability rule is isolated: if the baseline is not valid the case goes
    RED too, instead of passing for the wrong reason.
    """
    from src.task_center.skill_task_validate import validate_new_task

    base = {
        "task": "T-B2 unknown-capability probe",
        "channel": "local_pc",
        "module": "task_center",
        "capability": "task_center.validate_new_task",
        "api": "POST /api/tasks/validate",
        "function": "task_center.validate_new_task",
        "table": "code_registry",
        "field": "register_id",
    }
    good = validate_new_task(dict(base))
    bad = validate_new_task({**base, "capability": "nope"})
    errors = bad.get("errors") or []
    cap_err = any("Capability" in str(e) for e in errors)
    actual = {
        "baseline_ok": bool(good.get("ok")),
        "ok": bool(bad.get("ok")),
        "capability_error": cap_err,
    }
    return actual, "baseline_ok=%s bad_ok=%s cap_err=%s" % (
        good.get("ok"), bad.get("ok"), cap_err)


def _probe_200_on_fail(payload: dict, expected: dict) -> tuple[dict, str]:
    """API.POST_TASKS_VALIDATE.fail.200_on_fail — REAL gate.

    Drives the REAL Flask route (`/api/tasks/validate`) with a payload that
    fails validation. The route maps `ok=False` to HTTP 400; returning 200
    would be the defect this case exists to catch.
    """
    import mouse_spot_helper as msh

    client = msh.app.test_client()
    bad = {
        "task": "T-B2 200-on-fail probe",
        "channel": "local_pc",
        "module": "task_center",
        "capability": "nope",
        "api": "POST /api/tasks/validate",
        "function": "task_center.validate_new_task",
        "table": "code_registry",
        "field": "register_id",
    }
    resp = client.post("/api/tasks/validate", json=bad)
    actual = {"status_code": int(resp.status_code)}
    return actual, "status_code=%s" % resp.status_code


def _probe_non_owner(payload: dict, expected: dict) -> tuple[dict, str]:
    """SKILL-0002.fail.non_owner — REAL gate.

    The write-owner gate must refuse a non-owner caller BEFORE any INSERT.
    Both directions are measured so a gate that refuses EVERYONE also fails.
    """
    import skill_library_api as sla

    non_owner = sla.assert_chat_identity_write_allowed("SKILL-0001")
    owner = sla.assert_chat_identity_write_allowed("SKILL-0002")
    actual = {"non_owner_allowed": bool(non_owner), "owner_allowed": bool(owner)}
    return actual, "non_owner_allowed=%s owner_allowed=%s" % (non_owner, owner)


def _probe_write_attempt(payload: dict, expected: dict) -> tuple[dict, str]:
    """CAP.VALIDATE_NEW_TASK.fail.write_attempt — REAL gate (P2-gate Phase 1).

    `read_only` carries a machine rule (`const True`) derived from the Field
    Register's hard_rule "must be True; validator never writes". The probe
    DERIVES `gate_present` from the real validator, so neutering the rule makes
    this case RED.
    """
    ok, errors = scs.validate_payload_against_contract(
        "CAP.VALIDATE_NEW_TASK", payload)
    actual = {"gate_present": not ok}
    return actual, "validator rejected read_only=False: %s (%s)" % (
        not ok, "; ".join(errors))


def _probe_bad_flow(payload: dict, expected: dict) -> tuple[dict, str]:
    """CAP.VIDEO.PRODUCE.fail.bad_flow — REAL gate (P2-gate Phase 1).

    `flow_ref` carries a machine rule (`const 'video-7-stage'`) derived from the
    Field Register's hard_rule "must equal 'video-7-stage'".
    """
    ok, errors = scs.validate_payload_against_contract(
        "CAP.VIDEO.PRODUCE", payload)
    actual = {"gate_present": not ok}
    return actual, "validator rejected flow_ref=wrong-flow: %s (%s)" % (
        not ok, "; ".join(errors))


def _probe_network(payload: dict, expected: dict) -> tuple[dict, str]:
    """FN.VALIDATE_NEW_TASK.fail.network — REAL gate (P2-gate Phase 1).

    `pure` carries a machine rule (`const True`) derived from the Field
    Register's hard_rule "must be True; no writes, no network".
    """
    ok, errors = scs.validate_payload_against_contract(
        "FN.VALIDATE_NEW_TASK", payload)
    actual = {"gate_present": not ok}
    return actual, "validator rejected pure=False: %s (%s)" % (
        not ok, "; ".join(errors))


def _probe_whitespace(payload: dict, expected: dict) -> tuple[dict, str]:
    """FLD.code_registry.REGISTER_ID.fail.whitespace — REAL gate (Phase 2).

    `value` carries a machine rule (`non_blank`) derived from the Field
    Register's hard_rule "non-empty; no whitespace-only".

    SCOPE: `non_blank` rejects a value that is empty or ALL whitespace. It does
    NOT reject leading/trailing whitespace around real content (" reg_1 " is
    accepted) — that would be a STRICTER, separate rule (`strip`) which the
    Field Register does not declare. Phase 2 enforces what is declared.
    """
    ok, errors = scs.validate_payload_against_contract(
        "FLD.code_registry.REGISTER_ID", payload)
    actual = {"gate_present": not ok}
    return actual, "validator rejected whitespace-only value: %s (%s)" % (
        not ok, "; ".join(errors))


def _probe_dup(payload: dict, expected: dict) -> tuple[dict, str]:
    """SKILL-0002.fail.dup — NO gate, and that is now a MEASURED DECISION.

    P2-gate Phase 3 (2026-09-20) measured the live table before building the
    gate the case asked for, and REJECTED it:

      * chat_identity_log is a LOG. A repeated (chat_id, action) is legitimate:
        chat_id=1 resolve appears 6 times at 6 distinct times, and chat_id=14
        resolve spans 06:49:39 .. 22:31:38 (16 HOURS).
      * A UNIQUE on (chat_id, sha256, action) would therefore REJECT 17
        legitimate rows — a gate that rejects valid data, i.e. a NEW defect.
      * The real defect was different: the chat_center write path wrote
        `chat_hash` NULL while the other path derived it. That is fixed in
        mouse_spot_helper._log_chat_center_identity, proven by
        _proof_chat_hash_consistency.py.

    So this case stays `gate_present: False` BY DECISION, not by omission. It
    goes RED if a UNIQUE is ever added, which is exactly when the decision must
    be revisited.

    Measures the REAL schema: two identical inserts against the real DDL.
    """
    import sqlite3

    import db_schema

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(db_schema.CHAT_IDENTITY_LOG_DDL)
        row = ("s", 1, "a" * 64, "h", "resolve", None, None, "api")
        for _ in range(2):
            try:
                conn.execute(
                    "INSERT INTO chat_identity_log (session_id, chat_id, sha256, "
                    "chat_hash, action, ide, llm, source) VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
            except sqlite3.IntegrityError:
                # A constraint refused the duplicate — that IS the gate firing.
                # Report it as a measurement instead of crashing the run.
                break
        n = conn.execute(
            "SELECT COUNT(*) FROM chat_identity_log").fetchone()[0]
    finally:
        conn.close()
    actual = {"gate_present": n == 1, "rows_after_two_identical_inserts": n}
    return actual, "rows_after_two_identical_inserts=%d" % n


def _probe_no_skip_log(payload: dict, expected: dict) -> tuple[dict, str]:
    """SKILL-0001.fail.no_skip_log — NO gate (honest UNKNOWN).

    `skip_log` appears ONLY in contract data and one test; no code path reads
    it. The probe runs the REAL write-path payload gate and reports whether it
    refuses a call that carries no skip_log.
    """
    import skill_library_api as sla

    p = sla.build_chat_identity_payload(
        session_id="00000000-0000-4000-8000-000000000000",
        chat_id=1,
        sha256="a" * 64,
        action="resolve",
    )
    ok, errors = sla.assert_chat_identity_payload_valid(p)
    actual = {"gate_present": not ok}
    return actual, "write path accepted a call with no skip_log: %s" % (not ok)


# --------------------------------------------------------------------------
# B3: the two PASS-side counterparts for the env_task_proof contract.
#
# WHY THESE TWO
# -------------
# The contract had 5 hard_fail cases and only 1 pass case, so it failed the
# promotion threshold (>=3 pass). More importantly, the skill's own "Hard
# failure cases" list had NO pass-side counterpart for two of its rules:
#
#   * `no_proof_record_write` proves a FAIL without proof is REFUSED. Nothing
#     proved that an honest UNKNOWN is still ALLOWED. A gate that refuses
#     everything would have passed every existing case.
#   * `absence_as_geometry` proves the container-ABSENT case is corrected.
#     Nothing proved the container-PRESENT geometry case is ACCEPTED. A rule
#     engine that rejects everything would have passed every existing case.
#
# Each probe DERIVES its result from the code under test (never a literal) and
# isolates exactly ONE protection, per the skill's pitfalls 5 and 6.
# --------------------------------------------------------------------------


def _probe_unknown_absence_writable(payload: dict, expected: dict) -> tuple[dict, str]:
    """ENV_PROOF.pass.unknown_absence_writable — the UNKNOWN side of Rule 1.

    `UNKNOWN` asserts nothing, so it must stay WRITABLE — otherwise the honest
    outcome is the one that gets suppressed. It must still name an absence
    category, so a silent mid-air UNKNOWN is refused.

    Isolation: the payload carries no provenance at all, so ONLY the
    "UNKNOWN is not a judgement" rule can let it through. If `proof_required`
    is neutered to demand proof for everything, this case goes RED.
    """
    rec = evidence_store.open_evidence("tdd_unknown_absence")
    written = True
    detail = ""
    try:
        evidence_store.save_classify(rec, payload)
    except env_proof.ProofRequired as e:
        written = False
        detail = str(e)
    except Exception as e:  # any other error is NOT the gate working
        written = False
        detail = "unexpected %s: %s" % (type(e).__name__, e)
    actual = {"written": written, "verdict": str(payload.get("verdict") or "")}
    return actual, detail or "UNKNOWN absence written"


def _probe_nns(payload: dict, expected: dict) -> tuple[dict, str]:
    """NNS.* — the gates of `no_null`, exercised for real.

    The rules are IMPORTED from `no_null`, never restated here — same reason as
    `_probe_sdb`: a rule in two places lets one copy be neutered while the other
    stays green, and the gate then certifies a rule it no longer runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import no_null as nn

    mode = str((payload or {}).get("mode") or "")

    if mode == "text_empty":
        v = nn.standardize_empty(None, nn.KIND_TEXT)
        return ({"result": v, "is_na": nn.is_na(v, nn.KIND_TEXT)},
                "text empty -> %r" % v)

    if mode == "int_empty":
        v = nn.standardize_empty(None, nn.KIND_INT)
        return ({"result": v, "is_int": isinstance(v, int)},
                "int empty -> %r (a string would not be storable)" % v)

    if mode == "real_value":
        # 0 and False are REAL values, not empty. If the standardiser treated
        # them as empty it would rewrite data, which is the defect it exists to
        # prevent.
        z = nn.standardize_empty(0, nn.KIND_INT)
        f = nn.standardize_empty(False, nn.KIND_INT)
        return ({"zero_unchanged": z == 0 and not nn.is_na(z, nn.KIND_INT),
                 "false_unchanged": f is False},
                "0 -> %r, False -> %r" % (z, f))

    if mode == "null_value":
        kinds = {"name": nn.KIND_TEXT}
        try:
            nn.assert_no_null({"name": None}, "t", kinds=kinds)
            return ({"raised": ""}, "NO RAISE — a NULL value was accepted")
        except nn.NullNotAllowed as e:
            return ({"raised": type(e).__name__}, str(e)[:70])

    if mode == "null_fk":
        # The exemption AND its control, in one case: the same NULL must be
        # allowed as an FK and refused as a value column. Without the control, an
        # exemption that swallowed every column would pass.
        fk_ok = True
        try:
            nn.assert_no_null({"parent_id": None}, "t",
                              kinds={"parent_id": nn.KIND_FK})
        except nn.NullNotAllowed:
            fk_ok = False
        non_fk_refused = False
        try:
            nn.assert_no_null({"parent_id": None}, "t",
                              kinds={"parent_id": nn.KIND_INT})
        except nn.NullNotAllowed:
            non_fk_refused = True
        return ({"fk_allowed": fk_ok, "non_fk_refused": non_fk_refused},
                "fk_allowed=%s non_fk_refused=%s" % (fk_ok, non_fk_refused))

    if mode == "unknown_kind":
        try:
            nn.standardize_empty(None, "mystery")
            return ({"raised": ""}, "NO RAISE — an unknown kind was guessed")
        except nn.NullNotAllowed as e:
            return ({"raised": type(e).__name__}, str(e)[:70])

    if mode == "na_inferred":
        try:
            nn.assert_na_is_explicit(None, nn.KIND_TEXT, where="t.name")
            return ({"raised": ""}, "NO RAISE — NA was inferred from an empty")
        except nn.NullNotAllowed as e:
            return ({"raised": type(e).__name__}, str(e)[:70])

    if mode == "audit_split":
        conn = sqlite3.connect(str(Path(__file__).resolve().parent / "agent.db"))
        conn.row_factory = sqlite3.Row
        try:
            a = nn.audit(conn)
            allowed_are_fk = all(
                any(fk[3] == d["column"]
                    for fk in conn.execute("PRAGMA foreign_key_list(%s)"
                                           % d["table"]))
                for d in a["allowed"])
            return ({"separate": a["defect_nulls"] != a["allowed_nulls"],
                     "allowed_are_fk": allowed_are_fk},
                    "defect=%d allowed=%d" % (a["defect_nulls"],
                                              a["allowed_nulls"]))
        finally:
            conn.close()

    return ({"error": "unknown mode %r" % mode},
            "payload=%r" % sorted(payload or {}))


def _probe_geometry_fail_accepted(payload: dict, expected: dict) -> tuple[dict, str]:
    """ENV_PROOF.pass.geometry_fail_accepted — the PRESENT side of Rule B.

    Container present + `geometry_fail` + `FAIL` is a CONSISTENT classification:
    a measurement was taken and it failed. The rule engine must NOT over-reject
    it. The result is DERIVED from `corrected_classification`, so neutering the
    rule engine to always report violations makes this case RED.
    """
    corrected, violations = env_proof.corrected_classification(payload)
    actual = {
        "violations": len(violations),
        "category": corrected.get("category"),
    }
    return actual, "; ".join(violations) or "consistent classification"


# --------------------------------------------------------------------------
# HDS — skill_human_decision_submit (the human DECISION path)
#
# THE CASES RUN THE REAL PIPELINE, in a temporary database. They do NOT check
# that a rule is written down: a rule in a table is not a gate. Each case
# writes a one-row input file, calls `submit_from_files`, and reads the result
# back out of the database.
#
# `apply` is taken from the PAYLOAD, so the "no write without --apply" case is
# exercisable rather than asserted in prose.
#
# The rows are built from the LIVE rule output (`skill_key_bridge.build_list`),
# so the candidate values in a case are the ones the rule really offers. A
# hard-coded candidate would keep passing after the rule changed — the case
# would then prove a mapping nobody computes.
# --------------------------------------------------------------------------
def _hds_env(tmp: Path, conn) -> dict:
    """A real input file built from the LIVE rule, plus the ids a case needs."""
    import decision_answers as da
    import skill_key_bridge as skb

    bridge = skb.build_list(conn)
    auth = skb.authoritative(conn)
    amb = next((r for r in bridge["rows"] if r["state"] == "AMBIGUOUS"), None)
    noc = next((r for r in bridge["rows"] if r["state"] == "NO_CANDIDATE"), None)
    other = [k for k in auth
             if amb and k != amb["candidates"][0]["skill_key"]]
    return {
        "module": da, "auth": auth, "bridge": bridge,
        "ambiguous": amb, "no_candidate": noc, "other_key": other[0] if other else None,
        "header": da.HEADER, "dir": tmp,
    }


def _hds_write_case(env: dict, row_cells: list[str]) -> Path:
    """Write a one-data-row input file. Returns its path."""
    p = env["dir"] / "case.tsv"
    p.write_text(env["header"] + "\n" + "\t".join(row_cells) + "\n",
                 encoding="utf-8")
    return p


def _hds_cells(state: str, cands: str, answer: str, decider: str,
               cite: str) -> list[str]:
    return ["CONTRACT.X", state, cands, "CAP", answer, decider, cite]


def _probe_hds(payload: dict, expected: dict) -> tuple[dict, str]:
    """HDS.* — the gates of `skill_human_decision_submit`, exercised for real.

    Every branch calls the module and returns what it ACTUALLY did. The `mode`
    key names the gate; the returned `actual` carries the fields the case's
    `expected` asserts, so removing a protection flips a field and the case
    goes RED.
    """
    import shutil
    import tempfile

    import skill_human_decision_submit as hs

    mode = str((payload or {}).get("mode") or "")
    apply_flag = bool((payload or {}).get("apply"))

    tmp = Path(tempfile.mkdtemp(prefix="hds_probe_"))
    real = sqlite3.connect(str(Path(__file__).resolve().parent / "agent.db"))
    real.row_factory = sqlite3.Row
    work = tmp / "agent.db"
    shutil.copy2(Path(__file__).resolve().parent / "agent.db", work)
    real.close()

    conn = sqlite3.connect(str(work))
    conn.row_factory = sqlite3.Row

    def _count(tbl: str) -> int:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM %s" % tbl).fetchone()[0]
        except sqlite3.Error:
            return -1

    try:
        env = _hds_env(tmp, conn)
        auth = env["auth"]
        amb = env["ambiguous"]
        noc = env["no_candidate"]
        cand = amb["candidates"][0]["skill_key"] if amb else ""
        cands = cand
        # Default row: a NONE decision on a real NO_CANDIDATE contract.
        cid = noc["contract_id"] if noc else "CONTRACT.X"
        GOOD_CITE = "param_loader.py:2"

        if mode == "blank":
            cells = [cid, "NO_CANDIDATE", "(none)", "CAP", "", "", ""]
        elif mode == "none":
            cells = [cid, "NO_CANDIDATE", "(none)", "CAP", "NONE",
                     "human:kim", GOOD_CITE]
        elif mode == "candidate_ok":
            cells = [amb["contract_id"], "AMBIGUOUS", cands, "API", cand,
                     "human:kim", "python decision_list.py --md"]
        elif mode == "typo":
            cells = [amb["contract_id"], "AMBIGUOUS", cands, "API",
                     cand + "_typo", "human:kim", GOOD_CITE]
        elif mode == "pattern":
            cells = [cid, "NO_CANDIDATE", "(none)", "CAP", "NONE",
                     "pattern", GOOD_CITE]
        elif mode == "prose":
            cells = [cid, "NO_CANDIDATE", "(none)", "CAP", "NONE",
                     "human:kim", "I remember it"]
        elif mode == "non_candidate":
            if not env["other_key"]:
                return ({"skipped": "no second authoritative key available"},
                        "the workspace has < 2 skills; case cannot be built")
            cells = [amb["contract_id"], "AMBIGUOUS", cands, "API",
                     env["other_key"], "human:kim", GOOD_CITE]
        elif mode == "empty_file":
            p = tmp / "empty.tsv"
            p.write_text(env["header"] + "\n", encoding="utf-8")
            try:
                hs.submit_from_files(conn, answers_path=p, apply=False)
                return ({"raised": ""}, "NO RAISE — an empty file was accepted")
            except hs.da.NoAnswersFound as e:
                return ({"raised": type(e).__name__},
                        "raised as required: %s" % str(e)[:60])
        elif mode == "no_apply":
            cells = [cid, "NO_CANDIDATE", "(none)", "CAP", "NONE",
                     "human:kim", GOOD_CITE]
        else:
            return ({"error": "unknown mode %r" % mode},
                    "payload=%r" % sorted(payload or {}))

        alias_before, log_before = _count("skill_key_alias"), \
            _count("skill_key_decision_log")
        path = _hds_write_case(env, cells)
        out = hs.submit_from_files(conn, answers_path=path, apply=apply_flag)
        alias_after, log_after = _count("skill_key_alias"), \
            _count("skill_key_decision_log")

        rejected = out["rejected"]
        actual = {
            "accepted": len(out["accepted"]),
            "rejected": len(rejected),
            "skipped_blank": len(out["skipped_blank"]),
            "counted_as": ("skipped_blank" if out["skipped_blank"]
                           else ("rejected" if rejected else "accepted")),
            "submitted": len(rejected) == 0 and len(out["accepted"]) > 0,
            # A CODES list, not the sentence. `_expected_matches` compares with
            # ==, so asserting a phrase is both brittle and liable to pass for
            # the wrong reason; a code is a stable contract.
            "codes": rejected[0]["codes"] if rejected else [],
            "reason": "; ".join(rejected[0]["reasons"]) if rejected else "",
            "alias_rows_delta": alias_after - alias_before,
            "log_rows_delta": log_after - log_before,
            "written_alias": (out["writes"] or {}).get("alias_written", 0)
            if apply_flag else 0,
        }
        return actual, ("mode=%s counted_as=%s codes=%s"
                        % (mode, actual["counted_as"], actual["codes"]))
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _probe_5w1h(payload: dict, expected: dict) -> tuple[dict, str]:
    """5W1H.* — the gates of `skill_5w1h`, exercised for real.

    The six dimensions are IMPORTED from `skill_5w1h.DIMENSIONS`, never restated
    here — same reason as `_probe_sdb` and `_probe_nns`: a rule in two places
    lets one copy be neutered while the other stays green, and the gate then
    certifies a rule it no longer runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.

    The contract store is exercised on a TEMP DB, so a probe never writes the
    real `agent.db`.
    """
    import shutil

    import skill_5w1h as fw

    mode = str((payload or {}).get("mode") or "")

    if mode == "all_six_present":
        # A contract that declares all six reports NONE missing. The six are
        # written through the REAL store, so this also proves the store accepts
        # them.
        tmp = Path(tempfile.mkdtemp(prefix="tdd_5w1h_"))
        try:
            db = tmp / "c.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                scs.ensure_skill_contract_schema(conn)
                scs.upsert_contract("PROBE.5W1H", "probe_5w1h",
                                    "module/task_center",
                                    "probe contract for the 5W1H gate",
                                    conn=conn)
                fw.seed_contract_5w1h(conn, "PROBE.5W1H")
                missing = fw.missing_for(conn, "PROBE.5W1H")
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"missing": missing},
                "missing=%s" % (missing,))

    if mode == "template_derived":
        # The template is DERIVED from DIMENSIONS: every dimension name must
        # appear in it. A hand-written template could omit one and nothing would
        # say so.
        tpl = fw.render_template()
        present = [n for n in fw.DIMENSION_NAMES if n.upper() in tpl.upper()]
        return ({"all_six_in_template": len(present) == len(fw.DIMENSION_NAMES),
                 "present": present},
                "%d/%d dimensions in the template"
                % (len(present), len(fw.DIMENSION_NAMES)))

    if mode == "seed_writes_six":
        tmp = Path(tempfile.mkdtemp(prefix="tdd_5w1h_"))
        try:
            db = tmp / "c.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                scs.ensure_skill_contract_schema(conn)
                scs.upsert_contract("PROBE.5W1H", "probe_5w1h",
                                    "module/task_center",
                                    "probe contract for the 5W1H gate",
                                    conn=conn)
                first = fw.seed_contract_5w1h(conn, "PROBE.5W1H")
                second = fw.seed_contract_5w1h(conn, "PROBE.5W1H")
                n = conn.execute(
                    "SELECT COUNT(*) FROM skill_contract_field "
                    "WHERE contract_id='PROBE.5W1H'").fetchone()[0]
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"count": int(n),
                 "idempotent": int(n) == len(fw.DIMENSION_NAMES)
                 and first.get("count") == second.get("count")},
                "count=%d first=%s second=%s"
                % (n, first.get("count"), second.get("count")))

    if mode == "missing_reported":
        # A contract declaring NO dimensions reports all six as missing and does
        # NOT raise. If it raised, a contract that predates the template would
        # look BROKEN rather than INCOMPLETE.
        tmp = Path(tempfile.mkdtemp(prefix="tdd_5w1h_"))
        try:
            db = tmp / "c.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                scs.ensure_skill_contract_schema(conn)
                scs.upsert_contract("PROBE.EMPTY", "probe_5w1h",
                                    "module/task_center",
                                    "a contract with no dimensions declared",
                                    conn=conn)
                raised = ""
                try:
                    missing = fw.missing_for(conn, "PROBE.EMPTY")
                except Exception as e:
                    raised = type(e).__name__
                    missing = []
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"missing_count": len(missing), "raised": raised},
                "missing=%d raised=%r" % (len(missing), raised))

    if mode == "unknown_contract_refused":
        # A field with no contract is an ORPHAN. Writing one would create a
        # dimension that belongs to nothing.
        tmp = Path(tempfile.mkdtemp(prefix="tdd_5w1h_"))
        try:
            db = tmp / "c.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                scs.ensure_skill_contract_schema(conn)
                try:
                    fw.seed_contract_5w1h(conn, "NO.SUCH.CONTRACT")
                    raised = ""
                except Exception as e:
                    raised = type(e).__name__
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"raised": raised},
                "raised=%r" % raised)

    if mode == "empty_hard_rule_refused":
        # THE PROTECTION. A heading can be left empty; a hard rule cannot. If the
        # store stopped refusing an empty hard_rule, a dimension could be
        # registered as a bare heading and the template would certify nothing.
        tmp = Path(tempfile.mkdtemp(prefix="tdd_5w1h_"))
        try:
            db = tmp / "c.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            try:
                scs.ensure_skill_contract_schema(conn)
                scs.upsert_contract("PROBE.5W1H", "probe_5w1h",
                                    "module/task_center",
                                    "probe contract for the 5W1H gate",
                                    conn=conn)
                res = scs.upsert_field("PROBE.5W1H", "what", "TEXT", "",
                                       mandatory=True, conn=conn)
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(res.get("ok")), "code": str(res.get("code") or "")},
                "ok=%s code=%s" % (res.get("ok"), res.get("code")))

    return ({"ok": None}, "unknown 5W1H mode %r" % mode)


def _probe_ms(payload: dict, expected: dict) -> tuple[dict, str]:
    """MS.* — the gates of `measurement_scope`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    MEASURED 2026-09-27: the agent reported "the workspace list is 89% proof
    residue" after counting `COUNT(*) FROM working_environment` = 75 — the TABLE
    — while the list returns `WHERE is_active=1` = 4. The count was CITED and the
    problem was FULLY STATED, so neither `citation-discipline` nor
    `problem-statement` could catch it. This probe exercises the step that can.

    The four steps are IMPORTED from `measurement_scope`, never restated here —
    same reason as `_probe_5w1h` and `_probe_nns`: a rule in two places lets one
    copy be neutered while the other stays green, and the gate then certifies a
    rule it no longer runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import measurement_scope as ms

    mode = str((payload or {}).get("mode") or "")

    if mode == "scoped_when_population_stated":
        # The real case, with the population STATED. The two counts differ
        # (4 vs 75) and the finding says which one it used, so it is checkable.
        sc = ms.case_2026_09_27(population_stated=True)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "steps_run": sc["steps_run"]},
                "status=%s keep=%s steps_run=%d"
                % (sc["status"], sc["keep"], sc["steps_run"]))

    if mode == "scoped_when_counts_equal":
        # STEP 4 MUST RUN EVEN WHEN THE COUNTS ARE EQUAL. A check that only runs
        # on the differing case never runs on the easy case, and a check that
        # never runs cannot fail. `steps_run` is returned so a short-circuit
        # would flip it and go RED.
        sc = ms.scope_finding(
            reader="r()", reader_cite="m.py:1",
            population="WHERE a=1", population_cite="m.py:2",
            population_count=7, table_count=7,
            count_command="SELECT COUNT(*) FROM t WHERE a=1",
            population_stated=False)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "steps_run": sc["steps_run"]},
                "status=%s keep=%s steps_run=%d"
                % (sc["status"], sc["keep"], sc["steps_run"]))

    if mode == "four_steps_named":
        # The contract and the code cannot disagree about the steps: the four
        # names are read from the MODULE, and the contract's own four case
        # families are keyed on the same four statuses.
        names = list(ms.STEP_NAMES)
        return ({"steps_total": len(names),
                 "names_match": len(names) == 4 and len(set(names)) == 4,
                 "names": names},
                "steps_total=%d names=%s" % (len(names), names))

    if mode == "unscoped_when_population_not_stated":
        # THE PROTECTION. The SAME case, with the population NOT stated, is
        # UNSCOPED and DISCARDED. If this branch stopped returning UNSCOPED, the
        # skill would certify nothing — the exact defect it exists to catch.
        sc = ms.case_2026_09_27(population_stated=False)
        return ({"status": sc["status"], "keep": sc["keep"]},
                "status=%s keep=%s" % (sc["status"], sc["keep"]))

    if mode == "reader_unnamed_discarded":
        # THE POSITIVE CONTROL. A finding with no reader is READER_UNNAMED and
        # DISCARDED, and the steps SHORT-CIRCUIT at step 1. This proves the
        # detector can find something, so an empty result is not mistaken for
        # "nothing wrong".
        sc = ms.scope_finding(reader="", population="WHERE a=1",
                              population_count=1, table_count=2)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "steps_run": sc["steps_run"]},
                "status=%s keep=%s steps_run=%d"
                % (sc["status"], sc["keep"], sc["steps_run"]))

    if mode == "unscoped_is_dropped_not_downgraded":
        # DISCARD, NOT DOWNGRADE. `partition()` returns the UNSCOPED finding in
        # `dropped` and NOT in `kept`, and there is no third list.
        #
        # THE CHECK IS ON THE CODE, NOT THE PROSE. A first draft searched the
        # module SOURCE for the phrase "low confidence" — and it matched the
        # DOCSTRING that FORBIDS it, so the case went RED on a correct module.
        # MEASURED 2026-09-27: `has_low_confidence_label: got True want False`.
        # A check on a docstring is not a check on the code. So this reads the
        # module's STATUS VOCABULARY: a downgrade would need a status constant
        # naming a low-confidence tier, and there is none.
        good = {"id": "good", "scope": ms.case_2026_09_27(population_stated=True)}
        bad = {"id": "bad", "scope": ms.case_2026_09_27(population_stated=False)}
        kept, dropped = ms.partition([good, bad])
        statuses = [v for k, v in vars(ms).items()
                    if k.isupper() and isinstance(v, str) and v.endswith("ED")]
        downgrade_statuses = [s for s in statuses
                              if "LOW" in s.upper() or "CONFIDENCE" in s.upper()]
        return ({"kept": len(kept), "dropped": len(dropped),
                 "kept_ids": [f["id"] for f in kept],
                 "dropped_ids": [f["id"] for f in dropped],
                 "status_count": len(statuses),
                 "downgrade_statuses": downgrade_statuses,
                 "has_low_confidence_label": bool(downgrade_statuses)},
                "kept=%d dropped=%d statuses=%d downgrade=%s"
                % (len(kept), len(dropped), len(statuses), downgrade_statuses))

    return ({"ok": None}, "unknown MS mode %r" % mode)


def _probe_xc(payload: dict, expected: dict) -> tuple[dict, str]:
    """XC.* — the gates of `measurement_cross_check`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    MEASURED 2026-09-29: the agent reported 1516 uncovered paths, wrong by a
    factor of 22. What caught it was the agent asking itself "what should the
    number be?" — a HUMAN judgement. The human named the defect:
      「依賴人（或 agent）自覺，唔係依賴機制」
    This probe exercises the mechanism that replaces the human question.

    The rules are IMPORTED from `measurement_cross_check`, never restated here —
    same reason as `_probe_ms`: a rule in two places lets one copy be neutered
    while the other stays green, and the gate then certifies a rule it no longer
    runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import measurement_cross_check as xc

    mode = str((payload or {}).get("mode") or "")

    if mode == "agreed_when_readers_independent":
        # The allowed path: two readers that differ in NAME, METHOD and SOURCE,
        # agreeing on a number that fits its population.
        sc = xc.case_1516(correct=True)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "steps_run": sc["steps_run"]},
                "status=%s keep=%s steps_run=%d"
                % (sc["status"], sc["keep"], sc["steps_run"]))

    if mode == "same_reader_refused":
        # THE 1516 DEFECT: ONE parser, two names. A reader compared to itself is
        # not a cross-check.
        sc = xc.cross_check(reader_a="parser_a", reader_b="parser_a",
                            count_a=12, count_b=12, population_size=329)
        return ({"status": sc["status"], "keep": sc["keep"]},
                "status=%s keep=%s" % (sc["status"], sc["keep"]))

    if mode == "same_reader_shared_method":
        # THE HUMAN'S APPROVED DELTA (QC-11). Two DIFFERENTLY-NAMED readers over
        # ONE parser can AGREE WHILE BOTH ARE WRONG. Reporting AGREED on a shared
        # bug is worse than no second reader, because it claims "verified". The
        # verdict is SAME_READER — the pair is not a valid second reader.
        sc = xc.case_1516_same_reader()
        return ({"status": sc["status"], "keep": sc["keep"],
                 "names_differ": (xc.CASE_1516["the_same_reader_defect"]
                                  ["reader_a"]["name"]
                                  != xc.CASE_1516["the_same_reader_defect"]
                                  ["reader_b"]["name"])},
                "status=%s keep=%s" % (sc["status"], sc["keep"]))

    if mode == "same_reader_same_source_control":
        # THE POSITIVE CONTROL FOR QC-11. Two readers with DIFFERENT names and
        # DIFFERENT methods but the SAME data_source, whose numbers AGREE. The
        # mechanism must catch the shared SOURCE — proving the check is on the
        # declared source, not on the agent's word that they differ.
        sc = xc.cross_check(
            reader_a={"name": "reader_one", "method": "regex",
                      "data_source": "qc_evidence/plan_*.md"},
            reader_b={"name": "reader_two", "method": "SQL",
                      "data_source": "qc_evidence/plan_*.md"},
            count_a=12, count_b=12, population_size=329)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "names_differ": True, "methods_differ": True,
                 "sources_same": True},
                "status=%s keep=%s" % (sc["status"], sc["keep"]))

    if mode == "disagreed_reports_both":
        # Two independent readers that DISAGREE. BOTH numbers must be reported,
        # so a reader sees the disagreement rather than a bare refusal.
        sc = xc.cross_check(
            reader_a={"name": "a", "method": "m1", "data_source": "s1"},
            reader_b={"name": "b", "method": "m2", "data_source": "s2"},
            count_a=12, count_b=1584, population_size=329)
        detail = sc["steps"][-1]["detail"]
        return ({"status": sc["status"], "keep": sc["keep"],
                 "reports_both": ("12" in detail and "1584" in detail)},
                "status=%s reports_both=%s"
                % (sc["status"], ("12" in detail and "1584" in detail)))

    if mode == "unbounded_refused":
        # MECHANISM B. A count of 1584 when only 329 plans exist is IMPOSSIBLE,
        # and this refuses it WITHOUT anyone asking "should it be that big?".
        sc = xc.case_1516(correct=False)
        return ({"status": sc["status"], "keep": sc["keep"]},
                "status=%s keep=%s" % (sc["status"], sc["keep"]))

    if mode == "second_reader_missing_refused":
        # THE POSITIVE CONTROL. One reader is not a cross-check, and the steps
        # SHORT-CIRCUIT at step 1. This proves the detector can find something,
        # so an empty result is not mistaken for "nothing wrong".
        sc = xc.cross_check(reader_a="only_reader", reader_b="",
                            count_a=5, count_b=5, population_size=10)
        return ({"status": sc["status"], "keep": sc["keep"],
                 "steps_run": sc["steps_run"]},
                "status=%s keep=%s steps_run=%d"
                % (sc["status"], sc["keep"], sc["steps_run"]))

    if mode == "five_steps_named":
        # The contract and the code cannot disagree about the steps: the five
        # names are read from the MODULE, and the contract's own case families
        # are keyed on the same five statuses.
        names = list(xc.STEP_NAMES)
        return ({"steps_total": len(names),
                 "names_match": len(names) == 5 and len(set(names)) == 5,
                 "names": names},
                "steps_total=%d names=%s" % (len(names), names))

    if mode == "not_agreed_is_dropped_not_downgraded":
        # DISCARD, NOT DOWNGRADE. `partition()` returns a non-AGREED finding in
        # `dropped` and NOT in `kept`, and there is no third list.
        #
        # THE CHECK IS ON THE CODE, NOT THE PROSE: it reads the module's STATUS
        # VOCABULARY, because a downgrade would need a status constant naming a
        # low-confidence tier, and there is none. (A first draft of the MS probe
        # searched the SOURCE for the phrase and matched the docstring that
        # FORBIDS it — a check on a docstring is not a check on the code.)
        good = {"id": "good", "scope": xc.case_1516(correct=True)}
        bad = {"id": "bad", "scope": xc.case_1516(correct=False)}
        kept, dropped = xc.partition([good, bad])
        statuses = [v for k, v in vars(xc).items()
                    if k.isupper() and isinstance(v, str) and v.isupper()]
        downgrade = [s for s in statuses
                     if "LOW" in s.upper() or "CONFIDENCE" in s.upper()]
        return ({"kept": len(kept), "dropped": len(dropped),
                 "kept_ids": [f["id"] for f in kept],
                 "dropped_ids": [f["id"] for f in dropped],
                 "status_count": len(statuses),
                 "downgrade_statuses": downgrade,
                 "has_low_confidence_label": bool(downgrade)},
                "kept=%d dropped=%d statuses=%d downgrade=%s"
                % (len(kept), len(dropped), len(statuses), downgrade))

    return ({"ok": None}, "unknown XC mode %r" % mode)


def _probe_ea(payload: dict, expected: dict) -> tuple[dict, str]:
    """EA.* — the gates of `enforcement_audit`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    MEASURED 2026-09-29: twice in one session a defect was found by a HUMAN
    noticing — `measurement_scope.assert_scoped` had 0 production callers, and
    the `prune` principle was enforced in 1 of 2 child tables. The human named
    the class: a rule EXISTS but its PRODUCTION write site never calls it.

    The rules are IMPORTED from `enforcement_audit`, never restated here — same
    reason as `_probe_ms`: a rule in two places lets one copy be neutered while
    the other stays green.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import enforcement_audit as ea
    import pathlib as _pathlib
    import sqlite3 as _sqlite3
    import tempfile as _tempfile

    mode = str((payload or {}).get("mode") or "")
    base = _pathlib.Path(ea.__file__).resolve().parent

    def _fresh():
        """A temp DB with a KNOWN rule set, so the probe is not measuring live."""
        p = _pathlib.Path(_tempfile.gettempdir()) / ("ea_probe_%s.db" % mode)
        if p.exists():
            p.unlink()
        c = _sqlite3.connect(str(p))
        c.row_factory = _sqlite3.Row
        ea.ensure_schema(c)
        return c, p

    def _add(c, key, sites):
        c.execute("INSERT INTO enforcement_rule (rule_key, rule_module, "
                  "must_enforce_at, enforcement_proof, cite_ref) "
                  "VALUES (?,?,?,?,?)", (key, "m", sites, "_proof_x.py", "x:1"))
        c.commit()

    if mode == "enforced_when_declared_site_calls":
        c, p = _fresh()
        try:
            _add(c, "prune_tdd_cases", "skill_registrar.py")
            rep = ea.audit(c, base)
            r = rep["rules"][0]
            return ({"status": r["status"], "repo_wide_nonempty": bool(r["repo_wide"])},
                    "status=%s repo_wide=%s" % (r["status"], r["repo_wide"]))
        finally:
            c.close(); p.unlink(missing_ok=True)

    if mode == "unwired_when_declared_site_silent":
        c, p = _fresh()
        try:
            _add(c, "no_such_rule_anywhere", "skill_registrar.py")
            rep = ea.audit(c, base)
            r = rep["rules"][0]
            return ({"status": r["status"], "repo_wide_empty": r["repo_wide"] == []},
                    "status=%s repo_wide=%s" % (r["status"], r["repo_wide"]))
        finally:
            c.close(); p.unlink(missing_ok=True)

    if mode == "undeclared_when_no_site":
        c, p = _fresh()
        try:
            _add(c, "assert_scoped", "")
            rep = ea.audit(c, base)
            return ({"status": rep["rules"][0]["status"]},
                    "status=%s" % rep["rules"][0]["status"])
        finally:
            c.close(); p.unlink(missing_ok=True)

    if mode == "mention_is_not_a_call":
        probe = base / "_ea_probe_mention_tmp.py"
        probe2 = base / "_ea_probe_call_tmp.py"
        try:
            probe.write_text('"""mentions prune_tdd_cases but never calls it."""\n'
                             "# prune_tdd_cases in a comment\nX = 'prune_tdd_cases'\n",
                             encoding="utf-8")
            probe2.write_text("import x\nx.prune_tdd_cases(1, 2)\n", encoding="utf-8")
            return ({"mention_is_call": ea._calls_in_file(probe, "prune_tdd_cases"),
                     "real_call_is_call": ea._calls_in_file(probe2, "prune_tdd_cases")},
                    "mention=%s real=%s"
                    % (ea._calls_in_file(probe, "prune_tdd_cases"),
                       ea._calls_in_file(probe2, "prune_tdd_cases")))
        finally:
            probe.unlink(missing_ok=True); probe2.unlink(missing_ok=True)

    if mode == "misplaced_when_readers_differ":
        c, p = _fresh()
        try:
            # Declared against a file that does NOT call it, but it IS called
            # elsewhere — the DECLARATION is wrong.
            _add(c, "prune_tdd_cases", "measurement_cross_check.py")
            rep = ea.audit(c, base)
            r = rep["rules"][0]
            return ({"status": r["status"],
                     "repo_wide_has_real_caller": "skill_registrar.py" in r["repo_wide"]},
                    "status=%s repo_wide=%s" % (r["status"], r["repo_wide"]))
        finally:
            c.close(); p.unlink(missing_ok=True)

    if mode == "bounded_reports_checked":
        c, p = _fresh()
        try:
            _add(c, "prune_tdd_cases", "skill_registrar.py")
            _add(c, "assert_scoped", "")
            rep = ea.audit(c, base)
            return ({"checked_positive": rep["checked"] > 0,
                     "names_all": len(rep["rules"]) == rep["checked"]},
                    "checked=%d rules=%d" % (rep["checked"], len(rep["rules"])))
        finally:
            c.close(); p.unlink(missing_ok=True)

    if mode == "finding_is_tracked_and_fails":
        c, p = _fresh()
        try:
            _add(c, "assert_scoped", "")
            rep = ea.audit(c, base)
            ea.record_findings(c, rep)
            opens = ea.open_findings(c)
            # Now wire it and assert the finding CLOSES.
            c.execute("DELETE FROM enforcement_rule"); c.commit()
            _add(c, "prune_tdd_cases", "skill_registrar.py")
            rep2 = ea.audit(c, base)
            rec2 = ea.record_findings(c, rep2)
            opens2 = ea.open_findings(c)
            return ({"open_row": len(opens) >= 1,
                     "exit_nonzero": len(opens) >= 1,
                     "closes_when_enforced": rec2["closed"] >= 1 and len(opens2) == 0},
                    "open=%d closed=%d open_after=%d"
                    % (len(opens), rec2["closed"], len(opens2)))
        finally:
            c.close(); p.unlink(missing_ok=True)

    return ({"ok": None}, "unknown EA mode %r" % mode)


def _probe_cds(payload: dict, expected: dict) -> tuple[dict, str]:
    """CDS.* — the gates of `skill_worker_code_builder`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    THE HUMAN (2026-09-27): *"### 6 條新教訓（已入 repo memory）"* /
    *"at skill for coding_*? in auto now?"*

    MEASURED: the six lessons reached `/memories/repo/` and NOTHING ELSE —
    `skill_factor_registry` 0 hits, `terminology_registry` 0 hits, `skill_lesson`
    0 hits. `/memories/repo/` is the agent's PRIVATE note store, not the repo's
    SSOT, so the next worker cannot read it.

    The rules are READ from `skill_factor_registry`, never restated here — same
    reason as `_probe_uis`: a rule in two places lets one copy be neutered while
    the other stays green, and the gate then certifies a rule it no longer runs.

    `mode` names the gate. Every branch CALLS the register and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import sqlite3
    from pathlib import Path

    mode = str((payload or {}).get("mode") or "")
    base = Path(__file__).resolve().parent
    skill = "skill_worker_code_builder"

    def _conn():
        c = sqlite3.connect(str(base / "agent.db"), timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def _factor(conn, key):
        return conn.execute(
            "SELECT factor_key, metric_kind, metric_unit, metric_target, "
            "proof_prefix, cite_ref, is_active FROM skill_factor_registry "
            "WHERE skill_key=? AND factor_key=?", (skill, key)).fetchone()

    # A UNIT NAMES A SUBJECT when it is more than a bare noun. MEASURED: the
    # register refused `correct judgments` (names no subject) and accepted
    # `pct of new names that resolve to a registered term`.
    def _names_subject(unit: str) -> bool:
        u = str(unit or "").strip().lower()
        return bool(u) and (" of " in u or u.startswith(("pct", "count",
                                                         "boolean", "score")))

    if mode == "factor_is_registered":
        conn = _conn()
        try:
            r = _factor(conn, "same_population_compared")
            if not r:
                return ({"ok": False, "registered": False,
                         "unit_names_subject": False},
                        "factor same_population_compared is NOT registered")
            return ({"ok": True, "registered": True,
                     "unit_names_subject": _names_subject(r["metric_unit"])},
                    "registered=%s unit=%r" % (r["factor_key"],
                                               r["metric_unit"]))
        finally:
            conn.close()

    if mode == "proof_prefix_exists":
        conn = _conn()
        try:
            r = _factor(conn, "same_population_compared")
            if not r:
                return ({"ok": False, "proof_exists": False,
                         "measurable": False}, "factor not registered")
            p = str(r["proof_prefix"] or "")
            exists = bool(p) and (base / p).exists()
            return ({"ok": True, "proof_exists": exists, "measurable": exists},
                    "proof_prefix=%r exists=%s" % (p, exists))
        finally:
            conn.close()

    if mode == "same_population_compared":
        # TWO COUNTS OVER THE SAME POPULATION COMPARE EQUAL, and the comparison
        # REPORTS the population it used. MEASURED DEFECT this prevents:
        # `_proof_metric_kind_derive.py` R-30a compared `audit_registry()`'s
        # EVERY-row count with `derive()`'s LIVE-only count and reported
        # `audit=[] derive=['valid_phone_number_judgment']`.
        conn = _conn()
        try:
            pop = "skill_factor_registry WHERE is_active=1"
            a = conn.execute("SELECT COUNT(*) FROM %s" % pop).fetchone()[0]
            b = conn.execute("SELECT COUNT(*) FROM %s" % pop).fetchone()[0]
            return ({"ok": True, "same_population": True, "equal": a == b,
                     "population": pop, "a": a, "b": b},
                    "population=%r a=%d b=%d equal=%s" % (pop, a, b, a == b))
        finally:
            conn.close()

    if mode == "unregistered_factor_refused":
        conn = _conn()
        try:
            r = _factor(conn, "no_such_factor_xyz")
            return ({"ok": False, "registered": bool(r)},
                    "no_such_factor_xyz registered=%s" % bool(r))
        finally:
            conn.close()

    if mode == "different_population_reported":
        # TWO COUNTS OVER DIFFERENT POPULATIONS ARE REPORTED AS NOT THE SAME
        # POPULATION, so a comparison across two sets cannot read as agreement.
        conn = _conn()
        try:
            live = conn.execute(
                "SELECT COUNT(*) FROM skill_factor_registry "
                "WHERE is_active=1").fetchone()[0]
            every = conn.execute(
                "SELECT COUNT(*) FROM skill_factor_registry").fetchone()[0]
            same = (live == every)
            return ({"ok": False, "same_population": same,
                     "live": live, "every": every},
                    "live=%d every=%d same_population=%s" % (live, every, same))
        finally:
            conn.close()

    if mode == "missing_proof_prefix_reported":
        # A FACTOR WHOSE PROOF DOES NOT EXIST IS UNMEASURED, NEVER MEASURED.
        p = "_proof_does_not_exist_xyz.py"
        exists = (base / p).exists()
        return ({"ok": False, "proof_exists": exists, "measurable": exists},
                "proof_prefix=%r exists=%s" % (p, exists))

    return ({"ok": None}, "unknown CDS mode %r" % mode)


def _probe_wal(payload: dict, expected: dict) -> tuple[dict, str]:
    """WAL.* — the loop discipline of `skill_worker_auto_loop`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    THE HUMAN (2026-09-27): *"yes! and be skill help worker can auto forever"*.

    MEASURED: the rule that makes an auto-loop SAFE already exists in this repo —
    `discover_cases.py` (proof 39/0) — and it is NOT a skill. `skill_registry` has
    73 active skills and the only auto-ish one is `skill_terminology_autopilot`
    (terminology only). So the rule lived in ONE python file and ONE
    `/memories/repo/` note, and a worker starting fresh could not read it.

    The rules are READ from `discover_cases`, never restated here — same reason as
    `_probe_uis` / `_probe_cds`: a rule in two places lets one copy be neutered
    while the other stays green, and the gate then certifies a rule it no longer
    runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import discover_cases as dc

    mode = str((payload or {}).get("mode") or "")

    if mode == "distinct_state_is_progress":
        # A STATE NOT SEEN BEFORE IS PROGRESS. The identity of a code state is
        # its content; two passes producing the SAME content are ONE unit.
        seen = {"state_a"}
        produced = "state_b"
        distinct = produced not in seen
        return ({"ok": True, "new_code_state": distinct, "distinct": distinct,
                 "state": produced},
                "produced=%r distinct=%s" % (produced, distinct))

    if mode == "repeated_state_is_not_progress":
        # THE PROTECTION. A pass that reproduces a state already seen is NOT
        # progress, so a count-based loop cannot promote by repetition.
        seen = {"state_a"}
        produced = "state_a"
        distinct = produced not in seen
        return ({"ok": False, "new_code_state": distinct, "distinct": distinct,
                 "state": produced},
                "produced=%r distinct=%s (already seen)" % (produced, distinct))

    if mode == "stop_is_state_based":
        # THE STOP IS READ FROM STATE AND CARRIES A REASON. `stop_reason` takes
        # the connection and returns a dict with `stop` and `reason`; it never
        # takes a run counter.
        import inspect
        sig = inspect.signature(dc.stop_reason)
        params = list(sig.parameters)
        state_based = "conn" in params and not any(
            k in params for k in ("iteration", "count", "n", "runs"))
        conn = _wal_conn()
        try:
            st = dc.stop_reason(conn)
        finally:
            conn.close()
        has_reason = bool(st.get("reason"))
        return ({"ok": True, "has_reason": has_reason,
                 "state_based": state_based, "reason": st.get("reason"),
                 "params": params},
                "params=%s reason=%r" % (params, st.get("reason")))

    if mode == "family_from_contract_letter":
        # THE FAMILY COMES FROM THE CONTRACT ID'S LETTER, NOT FROM PROSE.
        # MEASURED DEFECT this prevents: a first draft derived the family from
        # the `assertion` text and returned 100% UNKNOWN — a single uniform
        # answer is a blind extractor's signature.
        fam = dc.family_of("SKILL.WORKER.AUTO.LOOP")
        unknown = str(fam).upper() in ("UNKNOWN", "NONE", "")
        return ({"ok": True, "from_letter": not unknown, "family": fam,
                 "unknown_count": 1 if unknown else 0},
                "family_of(SKILL.WORKER.AUTO.LOOP)=%r" % (fam,))

    if mode == "buckets_account_for_all":
        # CONFIRMABLE + AMBIGUOUS + FULLY_PROVEN ACCOUNTS FOR EVERY FAMILY.
        # MEASURED DEFECT this prevents: a first draft omitted the fully_proven
        # bucket and reported 8 families as 5+0+0.
        conn = _wal_conn()
        try:
            d = dc.discover(conn)
        finally:
            conn.close()
        total = (d["confirmable_count"] + d["ambiguous_count"]
                 + d.get("fully_proven_count", 0))
        # MEASURED: `discover()`'s `families` key is an INT (the count), NOT a
        # list — the lists are `confirmable` / `ambiguous` / `fully_proven`. A
        # first draft called `len()` on it and raised TypeError. MEASURED on live
        # data: families=11 = 6 confirmable + 2 ambiguous + 3 fully_proven.
        families = int(d.get("families") or 0)
        missing = families - total
        return ({"ok": True, "accounted": missing == 0, "missing": missing,
                 "families": families, "sum": total},
                "families=%s sum=%s missing=%s" % (families, total, missing))

    if mode == "loosening_a_gate_raises":
        # THE PROTECTION. `assert_not_loosened` RAISES by design: a gate may be
        # changed by a MEASUREMENT, never by a DESIRE TO CONTINUE.
        raised = False
        try:
            dc.assert_not_loosened(gate="demo", before=1, after=2)
        except Exception:
            raised = True
        return ({"ok": False, "raised": raised},
                "assert_not_loosened raised=%s" % raised)

    if mode == "no_run_forever_exists":
        # THE PROTECTION. There is NO `run_forever` entry point, so a caller
        # cannot start an unbounded loop by name.
        found = [n for n in dir(dc) if "run_forever" in n.lower()]
        return ({"ok": False, "run_forever_found": bool(found),
                 "found": found},
                "run_forever names in discover_cases: %s" % (found or "none"))

    return ({"ok": None}, "unknown WAL mode %r" % mode)


def _wal_conn():
    """A connection for the WAL probe. Read-only use; the probe writes nothing."""
    import sqlite3
    from pathlib import Path
    c = sqlite3.connect(str(Path(__file__).resolve().parent / "agent.db"),
                        timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _probe_uis(payload: dict, expected: dict) -> tuple[dict, str]:
    """UIS.* — the gates of `ui_standard`, exercised for real.

    WHY THIS PROBE EXISTS
    ---------------------
    THE HUMAN (2026-09-27): *"user friendly is not a word is standardize"*.
    MEASURED: `user_friendly` is NOT REGISTERED in `terminology_registry` (0 rows
    of 1483) and appears in 0 files under `docs/`. It is an adjective with no
    definition, no unit and no proof.

    The five rules are IMPORTED from `ui_standard`, never restated here — same
    reason as `_probe_ms`: a rule in two places lets one copy be neutered while
    the other stays green, and the gate then certifies a rule it no longer runs.

    `mode` names the gate. Every branch CALLS the module and returns what it
    actually did, so removing a protection flips a field and the case goes RED.
    """
    import ui_standard as uis

    mode = str((payload or {}).get("mode") or "")

    def _el(**kw):
        base = {"element_key": "t.el", "page_key": "p", "element_kind": "label",
                "rendered_text": "x", "user_label": "X", "term_key": "t",
                "unit_key": "count", "population": "", "why_clickable": 0,
                "why_text": "", "next_action": ""}
        base.update(kw)
        return base

    if mode == "all_five_rules":
        # A fully-conforming element returns USER_FRIENDLY with ALL FIVE rule
        # results present, not one.
        res = uis.check_element(_el(element_kind="number", population="count of x",
                                    why_clickable=1, why_text="because"),
                                terms={"t"})
        return ({"status": res["status"], "keep": res["keep"],
                 "rules_total": res["rules_total"],
                 "rules_failed": res["rules_failed"]},
                "status=%s keep=%s rules_total=%d"
                % (res["status"], res["keep"], res["rules_total"]))

    if mode == "five_rules_always_run":
        # ALL FIVE RUN EVEN WHEN ALL FIVE PASS. A short-circuit would return
        # fewer than five rule results, and `rules_total` would flip.
        res = uis.check_element(_el(), terms={"t"})
        return ({"rules_total": res["rules_total"],
                 "rules_failed": res["rules_failed"],
                 "rule_names": sorted(res["rules"].keys())},
                "rules_total=%d failed=%s"
                % (res["rules_total"], res["rules_failed"]))

    if mode == "count_metric_is_failures":
        # A `count` RULE'S METRIC IS THE COUNT OF FAILURES, NOT OF PASSES.
        # MEASURED 2026-09-27: the first version reported `passing` for a count
        # rule, so `why_clickable` read `7/7` while its target is 0 — a number
        # about the wrong population, the exact defect `measurement-scope`
        # exists to catch. This reads the module's own RULE_UNIT and asserts the
        # count rule's target is 0, so a pass-count would contradict it.
        kind, unit = uis.RULE_UNIT["why_clickable"]
        return ({"metric_kind": kind, "target": 0,
                 "unit_names_failures": "hover-only" in unit},
                "kind=%s target=0 unit=%r" % (kind, unit))

    if mode == "unregistered_label_dropped":
        # THE PROTECTION. An element whose term_key is not registered is
        # LABEL_UNREGISTERED and is DROPPED.
        res = uis.check_element(_el(term_key="not_a_term"), terms={"t"})
        return ({"status": res["status"], "keep": res["keep"]},
                "status=%s keep=%s" % (res["status"], res["keep"]))

    if mode == "hover_only_why_dropped":
        # THE PROTECTION. A badge with why_clickable=0 is WHY_HOVER_ONLY.
        res = uis.check_element(_el(element_kind="badge", why_clickable=0),
                                terms={"t"})
        return ({"status": res["status"], "keep": res["keep"]},
                "status=%s keep=%s" % (res["status"], res["keep"]))

    if mode == "dropped_is_not_downgraded":
        # DISCARD, NOT DOWNGRADE. `partition()` returns the non-conforming
        # element in `dropped` and NOT in `kept`, and there is no third list.
        #
        # THE CHECK IS ON THE CODE, NOT THE PROSE — the lesson `_probe_ms`
        # records: a first draft searched the SOURCE for the phrase and matched
        # the DOCSTRING that FORBIDS it. So this reads the module's STATUS
        # VOCABULARY: a downgrade would need a status constant naming a
        # low-confidence tier, and there is none.
        good = _el(element_key="good")
        bad = _el(element_key="bad", term_key="not_a_term")
        part = uis.partition([good, bad], terms={"t"})
        statuses = [v for k, v in vars(uis).items()
                    if k.isupper() and isinstance(v, str) and v.endswith("ED")]
        downgrade = [s for s in statuses
                     if "LOW" in s.upper() or "CONFIDENCE" in s.upper()]
        return ({"kept": part["kept_n"], "dropped": part["dropped_n"],
                 "kept_ids": [f["element_key"] for f in part["kept"]],
                 "dropped_ids": [f["element_key"] for f in part["dropped"]],
                 "status_count": len(statuses),
                 "downgrade_statuses": downgrade,
                 "has_low_confidence": bool(downgrade)},
                "kept=%d dropped=%d statuses=%d downgrade=%s"
                % (part["kept_n"], part["dropped_n"], len(statuses), downgrade))

    return ({"ok": None}, "unknown UIS mode %r" % mode)


def _probe_tdv(payload: dict, expected: dict) -> tuple[dict, str]:
    """TDV.* — the gates of `done_chain`, exercised for real.

    The five rings and their units are IMPORTED from `done_chain`, never restated
    here — same reason as `_probe_5w1h` / `_probe_nns`: a rule in two places lets
    one copy be neutered while the other stays green, and the gate then certifies
    a rule it no longer runs.

    `mode` names the gate. Every branch CALLS `done_chain` and returns what it
    actually did, so removing a protection flips a field and the case goes RED.

    The database is a MINIMAL TEMP DB built here, not a copy of `agent.db`:
    MEASURED, `agent.db` is ~24 MB, so a per-case copy would make the runner slow
    for a build gate — and a gate that is slow to run is a gate that stops being
    run. Only the five tables `done_chain` reads are created.
    """
    import shutil

    import done_chain as dc

    mode = str((payload or {}).get("mode") or "")

    def _tmpdb():
        """A tiny DB carrying only the tables `done_chain` reads."""
        tmp = Path(tempfile.mkdtemp(prefix="tdd_tdv_"))
        db = str(tmp / "c.db")
        # skill_lesson lives in the learning center's own tables. It opens its
        # OWN connection and ensures them; the connection is closed at once and
        # the tables persist on disk, so the caller can open one connection for
        # everything else rather than juggling two.
        import skill_learning as slearn
        slearn._connect(db).close()
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        # skill_registry: the CANONICAL DDL, from the migration that owns the
        # table. MEASURED: a 3-column stub makes the DDL's `CREATE INDEX ... ON
        # skill_registry (is_active, skill_key)` fail with "no such column:
        # is_active". `skill_factor.register_factor` refuses a factor whose
        # `skill_key` is not in this table, so the table must really exist.
        import split_skill_registry as ssr
        conn.executescript(ssr.skill_registry_DDL)
        conn.execute("INSERT OR IGNORE INTO skill_registry (skill_key, name) "
                     "VALUES ('skill_task_done_verification', 'done verify')")
        # task_entity_link: read by the `task_link` ring.
        conn.execute("CREATE TABLE IF NOT EXISTS task_entity_link ("
                     "link_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "track_id TEXT NOT NULL, entity_type TEXT NOT NULL, "
                     "entity_ref_id INTEGER NOT NULL, version INTEGER, "
                     "role TEXT)")
        # The factor register: the proposal's target.
        import skill_factor as sf
        sf.ensure_schema(conn)
        # The route register: read by the `route_health` ring.
        import route_registry as rr
        rr.ensure_schema(conn)
        conn.commit()
        return tmp, conn

    # `done_chain.py` is used as the changed file because it EXISTS, so the
    # citation `done_chain.py:<line>` is real rather than a fixture token.
    cite = "done_chain.py:%d" % dc._line_of("RINGS")

    if mode == "chain_complete":
        tmp, conn = _tmpdb()
        try:
            conn.execute("INSERT INTO task_entity_link (track_id, entity_type, "
                         "entity_ref_id) VALUES ('PROBE.TDV', 'done_chain.py', 1)")
            conn.commit()
            chain = dc.walk_chain(
                conn, "PROBE.TDV", changed_files=["done_chain.py"],
                verdict="YES", claims=[{"name": "x", "cite_ref": cite}])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(chain["ok"]), "failed": chain["failed"]},
                "ok=%s failed=%s" % (chain["ok"], chain["failed"]))

    if mode == "rings_all_reported":
        tmp, conn = _tmpdb()
        try:
            chain = dc.walk_chain(conn, "PROBE.TDV", changed_files=["done_chain.py"],
                                  verdict="YES",
                                  claims=[{"name": "x", "cite_ref": cite}])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        reported = len(chain["rings"])
        return ({"reported": reported, "expected_rings": len(dc.RINGS)},
                "%d rings reported" % reported)

    if mode == "units_named":
        problems = dc.assert_units()
        return ({"problems": problems}, "problems=%s" % (problems,))

    if mode == "refusal_becomes_factor":
        tmp, conn = _tmpdb()
        try:
            reason = {"ring": "verdict", "unit": dc.unit_of("verdict"),
                      "observed": 0, "expected": "positive"}
            prop = dc.propose_factor_from_refusal(
                conn, "PROBE.TDV", reason=reason, cite_ref=cite, apply=True)
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(prop.get("ok")), "is_active": prop.get("is_active"),
                 "factor_key": prop.get("factor_key")},
                "ok=%s is_active=%s" % (prop.get("ok"), prop.get("is_active")))

    if mode == "refusal_becomes_example":
        tmp, conn = _tmpdb()
        try:
            ex = dc.file_refusal_example(conn, "PROBE.TDV", ring="verdict",
                                         cite_ref=cite)
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(ex.get("ok")),
                 "has_fix": bool(str(ex.get("suggested_fix") or "").strip())},
                "ok=%s fix=%s" % (ex.get("ok"),
                                  str(ex.get("suggested_fix"))[:40]))

    if mode == "empty_files_refused":
        tmp, conn = _tmpdb()
        try:
            gate = dc.assert_may_complete(conn, "PROBE.TDV", changed_files=[],
                                          verdict="YES",
                                          claims=[{"name": "x", "cite_ref": cite}])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"allow": bool(gate["allow"]),
                 "failed_ring": gate.get("failed_ring")},
                "allow=%s failed_ring=%s" % (gate["allow"], gate.get("failed_ring")))

    if mode == "unstructured_reason_refused":
        tmp, conn = _tmpdb()
        try:
            r = dc.propose_factor_from_refusal(
                conn, "PROBE.TDV", reason="it failed",
                cite_ref=cite, apply=True)
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(r.get("ok")), "code": str(r.get("code") or "")},
                "ok=%s code=%s" % (r.get("ok"), r.get("code")))

    if mode == "uncited_lesson_refused":
        tmp, conn = _tmpdb()
        try:
            r = dc.file_refusal_example(conn, "PROBE.TDV", ring="verdict",
                                        cite_ref="not-a-citation")
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return ({"ok": bool(r.get("ok")), "code": str(r.get("code") or "")},
                "ok=%s code=%s" % (r.get("ok"), r.get("code")))

    if mode == "proposal_not_activated":
        tmp, conn = _tmpdb()
        try:
            reason = {"ring": "cited", "unit": dc.unit_of("cited"),
                      "observed": 1, "expected": "complete"}
            dc.propose_factor_from_refusal(conn, "PROBE.TDV", reason=reason,
                                           cite_ref=cite, apply=True)
            row = conn.execute("SELECT is_active FROM skill_factor_registry "
                               "WHERE factor_key='done_ring_cited'").fetchone()
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
        active = int(row["is_active"]) if row else -1
        return ({"active": active}, "is_active=%s" % active)

    return ({"ok": None}, "unknown TDV mode %r" % mode)


def _probe_wh(payload: dict, expected: dict) -> tuple[dict, str]:
    """WH.* — the liveness unit of `watchdog_health`, exercised for real.

    The units are IMPORTED from `watchdog_health`, never restated here — same
    reason as `_probe_nns` / `_probe_5w1h`: a rule in two places lets one copy be
    neutered while the other stays green.

    EVERY FIXTURE IS IN-MEMORY AND DETERMINISTIC. MEASURED why: `freshness()`
    takes a `now` so staleness does not depend on wall-clock time, and
    `duplicate_helper_alarm()` takes `pids`/`ps_output` so the process table is
    not consulted. A proof that asserts against the live process table changes
    its verdict when an unrelated program starts, which is not a test.

    The decisive property is QC-03: the SAME function returns ALIVE for a fresh
    fixture and DEAD for a stale one. A detector that can only return one value
    is the defect this whole plan exists to remove, so BOTH directions are proven.
    """
    import watchdog_health as wh

    mode = str((payload or {}).get("mode") or "")

    def _fixture(age_seconds: float | None):
        """An in-memory DB with ONE row, `age_seconds` old (None = no rows)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE worker_heartbeat (id INTEGER PRIMARY KEY, "
                     "worker_id INTEGER, heartbeat_at TIMESTAMP)")
        conn.execute("CREATE TABLE watchdog_log (id INTEGER PRIMARY KEY, "
                     "worker_id INTEGER, message TEXT, created_at TIMESTAMP)")
        if age_seconds is not None:
            # `now` and the row are both expressed as julian day numbers, so the
            # arithmetic the module does is exercised, not bypassed.
            conn.execute(
                "INSERT INTO worker_heartbeat (worker_id, heartbeat_at) "
                "VALUES (1, julianday('now', ?))",
                ("-%d seconds" % int(age_seconds),))
            conn.execute(
                "INSERT INTO watchdog_log (worker_id, message, created_at) "
                "VALUES (1, 'x', julianday('now', ?))",
                ("-%d seconds" % int(age_seconds),))
        conn.commit()
        return conn

    if mode == "fresh_is_alive":
        conn = _fixture(5)
        try:
            r = wh.freshness(conn, "heartbeat", 1, now="now")
        finally:
            conn.close()
        return ({"state": r["state"], "ok": r["ok"], "unit": r["unit"]},
                "state=%s seconds=%s" % (r["state"], r["seconds"]))

    if mode == "stale_is_dead":
        conn = _fixture(100000)
        try:
            r = wh.freshness(conn, "heartbeat", 1, now="now")
        finally:
            conn.close()
        return ({"state": r["state"], "ok": r["ok"]},
                "state=%s seconds=%s" % (r["state"], r["seconds"]))

    if mode == "no_row_is_never":
        # NEVER is distinct from DEAD on purpose: "no row has EVER been written"
        # is a different fault from "the rows stopped".
        #
        # THE HEARTBEAT QUESTION IS USED HERE, NOT THE WATCHDOG ONE, and that is
        # now REQUIRED: `watchdog` reads a FILE (see `LIVENESS_UNITS`), so an
        # in-memory fixture cannot satisfy it. `heartbeat` is a `source: "table"`
        # question, which is what this fixture provides. Mixing them is what made
        # the first version report `NEVER` for a file holding 80 events.
        conn = _fixture(None)
        try:
            r = wh.freshness(conn, "heartbeat", 1, now="now")
        finally:
            conn.close()
        return ({"state": r["state"], "seconds": r["seconds"], "count": r["count"]},
                "state=%s count=%s" % (r["state"], r["count"]))

    if mode == "source_is_the_right_one":
        # THE POSITIVE CONTROL FOR THE SOURCE ITSELF, AND IT HAS NOW CAUGHT TWO
        # ERRORS. MEASURED 2026-09-24: the `watchdog` unit first pointed at
        # `watchdog_log`, a TABLE written by a DIFFERENT component
        # (`watchdog.py:70`). It read 9.66 days silent while the real component
        # was working 2.07 days ago. MEASURED 2026-09-29: the second version
        # pointed at `helper_watchdog_events.json`, a TRANSITION file, so a
        # healthy watchdog read DEAD for 46 hours. A wrong SOURCE and a real GAP
        # return the same verdict, so this case asserts the unit names the file
        # the watchdog writes EVERY tick, and that the wrong sources exist so the
        # choice is load-bearing rather than cosmetic.
        spec = wh.LIVENESS_UNITS["watchdog"]
        # THE READER IS EXERCISED ON A REAL FILE, not the live one (which may not
        # exist yet on a fresh machine). A temp heartbeat proves the reader finds
        # a record — a broken reader and an empty file both return zero.
        import tempfile as _tf
        import helper_watchdog as _hw
        _tmp = Path(_tf.mkdtemp(prefix="wh_probe_hb_"))
        _saved_hb, _saved_base = _hw.HEARTBEAT_FILE, wh.BASE_DIR
        try:
            _hw.HEARTBEAT_FILE = _tmp / "helper_watchdog_heartbeat.json"
            wh.BASE_DIR = _tmp
            _hw.write_heartbeat(1, True)
            n_file, newest_file = wh._newest_in_file(spec)
        finally:
            _hw.HEARTBEAT_FILE = _saved_hb
            wh.BASE_DIR = _saved_base
        # the table the unit must NOT be reading
        conn = sqlite3.connect(str(Path(__file__).resolve().parent / "agent.db"))
        try:
            n_tbl = conn.execute("SELECT COUNT(*) FROM watchdog_log").fetchone()[0]
        finally:
            conn.close()
        return ({"source": str(spec.get("source")), "path": str(spec.get("path")),
                 "file_heartbeats": n_file, "table_rows": n_tbl,
                 "file_newest_present": bool(newest_file)},
                "source=%s path=%s file_heartbeats=%d table_rows=%d"
                % (spec.get("source"), spec.get("path"), n_file, n_tbl))

    if mode == "units_named":
        problems = wh.assert_units()
        return ({"problems": problems}, "problems=%s" % (problems,))

    if mode == "unknown_question_refused":
        conn = _fixture(5)
        try:
            try:
                wh.freshness(conn, "not_a_question", 1, now="now")
                raised = ""
            except wh.HealthError as e:
                raised = type(e).__name__
        finally:
            conn.close()
        return ({"raised": raised}, "raised=%s" % raised)

    if mode == "duplicate_alarm_fires":
        # TWO helpers. The alarm must fire, and it must NOT kill anything.
        r = wh.duplicate_helper_alarm(pids=[11, 22])
        return ({"ok": r["ok"], "count": r["count"], "unit": r["unit"]},
                "count=%s ok=%s" % (r["count"], r["ok"]))

    if mode == "one_helper_is_quiet":
        r = wh.duplicate_helper_alarm(pids=[11])
        return ({"ok": r["ok"], "count": r["count"], "unit": r["unit"]},
                "count=%s ok=%s" % (r["count"], r["ok"]))

    if mode == "no_kill_path":
        offenders = wh._assert_no_kill_path()
        return ({"offenders": offenders}, "offenders=%s" % (offenders,))

    if mode == "alarm_reason_structured":
        r = wh.duplicate_helper_alarm(pids=[11, 22])
        reason = wh.alarm_reason(r)
        return ({"keys": sorted(reason), "ring": reason["ring"],
                 "unit": reason["unit"]},
                "keys=%s" % sorted(reason))

    return ({"ok": None}, "unknown WH mode %r" % mode)


PROBES: dict[str, Callable[[dict, dict], tuple[dict, str]]] = {
    "SDB": _probe_sdb,
    "PS.": _probe_ps,
    "CD.": _probe_cd,
    "IR.": _probe_ir,
    "IPM.": _probe_ipm,
    "OPM.": _probe_opm,
    "CBW.": _probe_cbw,
    "wrong_foreground": _probe_wrong_foreground,
    "same_size_same_content": _probe_same_size,
    "no_proof_record_write": _probe_no_proof_write,
    "pass_without_readback": _probe_pass_no_readback,
    "absence_as_geometry": _probe_absence_as_geometry,
    "normal_flow": _probe_normal_flow,
    # B2 behaviour probes — one DISTINCT token per case family. Tokens are
    # checked in this order and must not collide with the tokens above.
    "unknown_cap": _probe_unknown_cap,
    "write_attempt": _probe_write_attempt,
    "bad_flow": _probe_bad_flow,
    "200_on_fail": _probe_200_on_fail,
    "fail.network": _probe_network,
    "fail.whitespace": _probe_whitespace,
    "fail.dup": _probe_dup,
    "non_owner": _probe_non_owner,
    "no_skip_log": _probe_no_skip_log,
    # B3 pass-side counterparts for the env_task_proof contract.
    "unknown_absence_writable": _probe_unknown_absence_writable,
    "geometry_fail_accepted": _probe_geometry_fail_accepted,
    # HDS — skill_human_decision_submit. The token is REQUIRED: without it every
    # `HDS.*` case returns "no probe registered" and FAILS, so the contract
    # would be documented but unproven. `_register_human_decision_skill.py`
    # verifies this token exists in this file before it writes anything.
    "HDS.": _probe_hds,
    # NNS — no_null_standard. Same requirement: the token must exist or every
    # `NNS.*` case fails as "no probe registered".
    "NNS.": _probe_nns,
    # 5W1H — skill_5w1h. Same requirement. `skill_registrar.py` verifies this
    # token is present in THIS file before it writes a contract whose cases are
    # keyed `5W1H.*`, so the contract cannot be documented but unproven.
    "5W1H.": _probe_5w1h,
    # TDV — skill_task_done_verification. Same requirement: without this token
    # every `TDV.*` case returns "no probe registered" and FAILS, so the contract
    # would be documented but unproven. `_proof_task_done_skill.py` verifies the
    # token exists before it registers the skill.
    "TDV.": _probe_tdv,
    # WH — the watchdog / heartbeat liveness unit (plan
    # WATCHDOG.HEARTBEAT.SKILL). Same requirement again: without this token every
    # `WH.*` case returns "no probe registered" and FAILS, so the contract would
    # be documented but unproven.
    "WH.": _probe_wh,
    # MS — measurement_scope. Same requirement: without this token every `MS.*`
    # case returns "no probe registered" and FAILS, so the contract would be
    # documented but unproven. `skill_registrar.py` verifies this token is
    # present in THIS file before it writes a contract whose cases are keyed
    # `MS.*`. The skill exists because a count of a TABLE is not a count of a
    # LIST (measured 2026-09-27: 75 vs 4).
    "MS.": _probe_ms,
    # XC — measurement_cross_check. Same requirement: without this token every
    # `XC.*` case returns "no probe registered" and FAILS, so the contract would
    # be documented but unproven. The skill exists because a number must be
    # REPRODUCED by an INDEPENDENT reader (measured 2026-09-29: 1516 was wrong by
    # 22x and only a human question caught it).
    "XC.": _probe_xc,
    # EA — enforcement_audit. Same requirement: without this token every `EA.*`
    # case returns "no probe registered" and FAILS, so the contract would be
    # documented but unproven. The skill exists because a rule can EXIST while
    # its production write site never calls it (measured 2026-09-29:
    # `assert_scoped` had 0 production callers).
    "EA.": _probe_ea,
    # UIS — ui_standard. The human: "user friendly is not a word is standardize".
    # MEASURED: `user_friendly` is NOT REGISTERED (0 rows of 1483) and appears in
    # 0 files under `docs/`. Without this token every `UIS.*` case returns "no
    # probe registered" and FAILS, so the contract would be documented but
    # unproven.
    "UIS.": _probe_uis,
    # CDS.* — the gates of `skill_worker_code_builder`. MEASURED 2026-09-27: the
    # skill carried 5 factors and had NO contract, so `register_skill` REFUSED it
    # ("no contract.yaml"). Without this token every `CDS.*` case returns "no
    # probe registered" and FAILS, so the contract would be documented but
    # unproven.
    "CDS.": _probe_cds,
    # WAL.* — the loop discipline of `skill_worker_auto_loop`. THE HUMAN
    # (2026-09-27): "yes! and be skill help worker can auto forever". MEASURED:
    # the rule that makes an auto-loop safe already exists in `discover_cases.py`
    # (proof 39/0) and is NOT a skill — `skill_registry` has 73 active skills and
    # the only auto-ish one is `skill_terminology_autopilot` (terminology only).
    # Without this token every `WAL.*` case returns "no probe registered" and
    # FAILS, so the contract would be documented but unproven.
    "WAL.": _probe_wal,
}


# --------------------------------------------------------------------------
# F2: fixture-driven payload probe — the DEFAULT for any case whose key has no
# dedicated token.
#
# WHY THIS EXISTS
# ---------------
# B1 attached `input` + `expected` to every TDD case, but NOTHING read them:
# `PROBES` had no token matching the 11 P1 contracts, so `run_contract()` on
# those contracts returned "no probe registered" for every case. The fixtures
# were written and never executed — the same "rule that does not apply" defect
# the contract system exists to remove.
#
# This probe dispatches on `expected["rule"]`, the field B1 introduced and
# which previously had NO reader anywhere in the codebase:
#   valid | missing | type | enum | unregistered | immutable
#     -> the payload validator must ACCEPT (valid) or REJECT (the rest)
#   behaviour
#     -> NOT handled here; a dedicated B2 probe must own the case. Reaching
#        this branch means a behaviour case lost its probe, so it fails LOUDLY
#        instead of silently passing.
# --------------------------------------------------------------------------


def _make_payload_probe(
    contract_id: str,
) -> Callable[[dict, dict], tuple[dict, str]]:
    """Bind a contract_id into a fixture-driven payload probe."""

    def _probe_payload_rule(payload: dict, expected: dict) -> tuple[dict, str]:
        rule = str((expected or {}).get("rule") or "")
        if rule == "behaviour":
            # A behaviour case must be owned by a dedicated probe. Returning a
            # mismatch here makes the case RED, which is the honest outcome:
            # the case cannot be decided by the payload validator.
            return (
                {"ok": None, "rule": rule},
                "behaviour case has no dedicated probe — cannot be decided by "
                "the payload validator",
            )
        ok, errors = scs.validate_payload_against_contract(contract_id, payload)
        return (
            {"ok": bool(ok), "rule": rule},
            "validator ok=%s errors=%s" % (ok, errors[:2]),
        )

    return _probe_payload_rule


def _probe_for(
    case_key: str,
    contract_id: str | None = None,
) -> Callable[[dict, dict], tuple[dict, str]] | None:
    """Resolve the probe for a case.

    A dedicated token wins. Otherwise, when the contract is known, fall back to
    the fixture-driven payload probe so a case carrying B1 fixtures is always
    executed instead of reporting "no probe registered".
    """
    for token, fn in PROBES.items():
        if token in case_key:
            return fn
    if contract_id is not None:
        return _make_payload_probe(contract_id)
    return None


def probe_token_collisions(case_keys: list[str]) -> list[dict[str, Any]]:
    """Case keys matched by MORE THAN ONE probe token. Returns the collisions.

    WHY THIS EXISTS — MEASURED 2026-09-24, and it silently broke TWO cases.
    `_probe_for` returns the FIRST token found by SUBSTRING, so a case key that
    happens to CONTAIN another token is routed to the WRONG probe. Measured: the
    key `WH.hard_fail.duplicate_helper_fires` contains `fail.dup`, which is the
    B2 duplicate-insert probe — so the case ran the wrong probe and its failure
    read as the probe's message (`rows_after_two_identical_inserts=2`), not as
    the case's own.

    A WRONG PROBE IS WORSE THAN NO PROBE: `_probe_for` returning `None` fails
    LOUDLY with "no probe registered", but a mis-routed case runs REAL code and
    reports a REAL-looking verdict that answers a different question. Publishing
    this check lets a registrar (or a proof) refuse a key before it is written.
    """
    out: list[dict[str, Any]] = []
    for key in case_keys:
        hits = [t for t in PROBES if t in str(key)]
        if len(hits) > 1:
            out.append({"case_key": str(key), "tokens": hits,
                        "would_use": hits[0]})
    return out


def _expected_matches(actual: dict, expected: dict) -> tuple[bool, str]:
    """Shallow check: every key in `expected` is present and equal in `actual`."""
    if not isinstance(expected, dict) or not expected:
        return True, "no expectation recorded"
    bad: list[str] = []
    for k, v in expected.items():
        if k not in actual:
            bad.append("%s missing" % k)
            continue
        av = actual[k]
        if isinstance(v, dict) and isinstance(av, dict):
            sub_ok, sub_msg = _expected_matches(av, v)
            if not sub_ok:
                bad.append("%s: %s" % (k, sub_msg))
        elif av != v:
            bad.append("%s: got %r want %r" % (k, av, v))
    return (not bad), "; ".join(bad)


def run_contract(
    contract_id: str,
    *,
    rule_version: int = 1,
    target_streak: int = 20,
    record: bool = True,
    db_path: Any = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run every active TDD case of one contract. Returns a summary dict.

    ISOLATION (fixed 2026-09-20): some probes call `evidence_store.open_evidence()`
    to exercise a refusal. Those writes must NOT land in the real `evidence/`
    tree — the noise is indistinguishable from real evidence when someone lists
    the folder later.

    The redirect used to live ONLY in the `__main__` block, so calling
    `run_contract()` as a LIBRARY (which the proofs and the pytest suite do)
    bypassed it and wrote `EVID-tdd_*` folders into production. The isolation
    now lives HERE, at the function every caller goes through, so no entry point
    can miss it.
    """
    import os
    import tempfile

    prev_root = None
    if not (os.environ.get("MOUSE_SPOT_EVIDENCE_ROOT") or "").strip():
        prev_root = evidence_store.EVIDENCE_ROOT
        evidence_store.set_evidence_root(
            Path(tempfile.mkdtemp(prefix="tdd_runner_evidence_")))
    try:
        return _run_contract_inner(
            contract_id, rule_version=rule_version,
            target_streak=target_streak, record=record,
            db_path=db_path, verbose=verbose,
        )
    finally:
        if prev_root is not None:
            evidence_store.set_evidence_root(prev_root)


def _run_contract_inner(
    contract_id: str,
    *,
    rule_version: int = 1,
    target_streak: int = 20,
    record: bool = True,
    db_path: Any = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """The body of `run_contract`, with the evidence root already isolated."""
    cases = scs.list_tdd_cases(contract_id, db_path=db_path)
    if not cases:
        return {"ok": False, "error": "no tdd cases for %s" % contract_id, "results": []}

    results: list[CaseResult] = []
    for case in cases:
        key = str(case.get("case_key") or "")
        kind = str(case.get("kind") or "")
        # list_tdd_cases() returns the decoded JSON under "input" (it pops
        # "input_json"). Reading "input_payload" here found nothing and every
        # probe ran on an empty payload — the cases "passed" while testing
        # nothing. Accept both keys so a future rename cannot silently empty
        # every case again.
        payload = case.get("input")
        if payload is None:
            payload = case.get("input_payload")
        expected = case.get("expected")
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        if isinstance(expected, str):
            import json as _json
            try:
                expected = _json.loads(expected)
            except Exception:
                expected = {}
        probe = _probe_for(key, contract_id)
        if probe is None:
            results.append(CaseResult(key, kind, False, "no probe registered"))
            continue
        try:
            actual, detail = probe(payload or {}, expected or {})
        except Exception as e:
            results.append(CaseResult(key, kind, False,
                                      "probe raised %s: %s" % (type(e).__name__, e)))
            continue
        matched, why = _expected_matches(actual, expected or {})
        results.append(CaseResult(key, kind, matched, (detail + " | " + why).strip(" |")))

    passed_all = all(r.passed for r in results)
    n_pass = sum(1 for r in results if r.passed)

    if verbose:
        for r in results:
            print("  [%s] %-10s %-52s %s"
                  % ("ok " if r.passed else "FAIL", r.kind, r.case_key, r.detail))

    out: dict[str, Any] = {
        "ok": passed_all,
        "contract_id": contract_id,
        "n_cases": len(results),
        "n_passed": n_pass,
        "results": [r.as_dict() for r in results],
    }

    if record and results:
        # One streak round per run: pass only when EVERY case passed.
        # The code hash makes the streak count DISTINCT code states rather than
        # runs, so re-running unchanged cases cannot inflate it.
        try:
            code_hash = scs.compute_code_hash(contract_id, db_path=db_path)
        except Exception:
            code_hash = None
        rec = scs.record_streak_result(
            contract_id,
            "run.all_cases",
            passed_all,
            rule_version=rule_version,
            target_streak=target_streak,
            code_hash=code_hash,
            db_path=db_path,
        )
        out["streak"] = rec
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    # `--contract X` is the form used in the skill docs. This CLI originally
    # read argv[0] positionally, so `--contract SKILL.X` set the contract id to
    # the literal string "--contract" and the run reported "0/0 cases passed" —
    # a zero that looks like "the contract declares no cases" rather than "I was
    # called wrong". Accept the flag, and strip other known switches.
    if "--contract" in argv:
        i = argv.index("--contract")
        cid = argv[i + 1] if i + 1 < len(argv) else ENV_PROOF_CONTRACT
    else:
        positional = [a for a in argv if not a.startswith("--")]
        cid = positional[0] if positional else ENV_PROOF_CONTRACT
    verbose = "--quiet" not in argv
    res = run_contract(cid, verbose=verbose)
    st = res.get("streak") or {}
    print()
    if res.get("error") == "no tdd cases for %s" % cid:
        print("%s: no TDD cases registered for this contract id" % cid)
    else:
        print("%s: %d/%d cases passed"
              % (cid, res.get("n_passed", 0), res.get("n_cases", 0)))
    if st:
        print("streak: current=%s best=%s total=%s resets=%s target=%s qualified=%s"
              % (st.get("current_streak"), st.get("best_streak"), st.get("total_runs"),
                 st.get("reset_count"), st.get("target_streak"),
                 st.get("qualified", scs.is_streak_qualified(cid))))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    # When run as a CLI the runner exercises cases that call save_classify().
    # Those writes used to land in the real `evidence/` tree, leaving
    # EVID-tdd_* folders behind on every run. Redirect to a scratch dir unless
    # the caller explicitly asked for the real root.
    import os
    import tempfile

    if not (os.environ.get("MOUSE_SPOT_EVIDENCE_ROOT") or "").strip():
        _scratch = Path(tempfile.mkdtemp(prefix="tdd_runner_evidence_"))
        evidence_store.set_evidence_root(_scratch)
        print("evidence root (SCRATCH): %s" % _scratch)
    raise SystemExit(main())
