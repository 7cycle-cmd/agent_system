"""test_env_proof_gate.py — permanent tests for the env_task_proof gate.

Two suites in one file:

  1. WRITE GATE    — the gate must REFUSE an unproven judgement, and must still
                     allow the honest UNKNOWN absence outcome.
  2. MUTATION      — break each protection and assert the TDD cases go RED.

Why the mutation suite is not optional: a green test suite proves nothing until
you have seen it fail. Every protection here was neutered at runtime and the
corresponding case was confirmed to fail. Without that step these cases are
theatre — which is exactly the defect this whole task exists to remove.

Run:  .\\.venv\\Scripts\\python.exe test_env_proof_gate.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import env_proof
import evidence_store
import skill_tdd_runner as runner

# ISOLATE EVIDENCE WRITES. This suite calls open_evidence() to exercise the
# gate, and every call used to create a real folder under `evidence/`. Hundreds
# of EVID-tdd_noproof-* / EVID-gate_* folders accumulated in the production
# tree. A test must not write into a real output directory.
_PROBE_ROOT = Path(tempfile.mkdtemp(prefix="env_proof_test_evidence_"))
_PREV_ROOT = evidence_store.set_evidence_root(_PROBE_ROOT)
assert not evidence_store.is_default_root(), "test must not use the real evidence root"
print("evidence root (TEST): %s" % _PROBE_ROOT)

CONTRACT = "MOD.MOUSE_SPOT_HELPER.ENV_PROOF"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s  %s" % ("ok " if cond else "FAIL", name, detail))


# ---------------------------------------------------------------------------
# 1. write gate
# ---------------------------------------------------------------------------

def suite_write_gate() -> None:
    print("=== GATE: a judgement without proof must be refused ===")
    rec = evidence_store.open_evidence("gate_probe")

    real_failure = {
        "verdict": "FAIL",
        "label": "Default permissions",
        "picker_ok": True,
        "edges_all_pass": False,
        "questions": {"q2_text_in_box": {"answer": "NO",
                                         "reason": "reads 'VS code'"}},
        # provenance exactly as recorded in the real failing run: it LOOKED ok
        "provenance": {"source": "pyautogui", "width": 1920, "height": 1080,
                       "sha256": "68dcae3ebf04", "real_screen": [1920, 1080]},
        "root_cause": {"category": "geometry_fail", "action": "re-measure"},
    }

    refused = False
    reasons: list[str] = []
    try:
        evidence_store.save_classify(rec, real_failure)
    except env_proof.ProofRequired as e:
        refused = True
        reasons = e.reasons
    check("FAIL without foreground proof is refused", refused)
    check("refusal names the missing foreground",
          any("foreground" in r for r in reasons), str(reasons))
    check("nothing written for the refused verdict",
          not Path(rec.dir, "classify.json").exists())

    good = dict(real_failure)
    good["provenance"] = dict(real_failure["provenance"])
    good["provenance"]["foreground"] = {
        "process": "Code.exe", "title": "Visual Studio Code", "is_code": True}
    good["provenance"]["foreground_is_code"] = True
    try:
        p = evidence_store.save_classify(rec, good)
        check("FAIL with full proof IS written", Path(p).exists())
    except env_proof.ProofRequired as e:
        check("FAIL with full proof IS written", False, str(e))

    print("=== GATE: absence stays recordable, and must say why ===")
    rec2 = evidence_store.open_evidence("gate_absence")
    try:
        p2 = evidence_store.save_classify(rec2, {
            "verdict": "UNKNOWN", "picker_ok": False,
            "picker_reason": "no permissions menu visible",
            "root_cause": {"category": "picker_not_open"}})
        check("UNKNOWN picker_not_open is writable", Path(p2).exists())
    except env_proof.ProofRequired as e:
        check("UNKNOWN picker_not_open is writable", False, str(e))

    rec3 = evidence_store.open_evidence("gate_silent_unknown")
    silent_refused = False
    try:
        evidence_store.save_classify(rec3, {
            "verdict": "UNKNOWN", "picker_ok": False,
            "picker_reason": "no menu visible"})
    except env_proof.ProofRequired:
        silent_refused = True
    check("UNKNOWN with no classification is refused (no silent unknown)",
          silent_refused)

    print("=== GATE: classification consistency ===")
    ok, why = env_proof.absence_beats_geometry("picker_not_open", "FAIL")
    check("picker_not_open + FAIL rejected", not ok, why)
    ok, why = env_proof.absence_beats_geometry("geometry_fail", "UNKNOWN")
    check("geometry_fail + UNKNOWN rejected", not ok, why)
    _, viol = env_proof.corrected_classification(
        {"picker_ok": False, "verdict": "FAIL",
         "root_cause": {"category": "geometry_fail"}})
    check("container absent + geometry claim is a violation", bool(viol),
          str(viol))
    _, viol_ok = env_proof.corrected_classification(
        {"picker_ok": False, "verdict": "UNKNOWN",
         "root_cause": {"category": "picker_not_open"}})
    check("container absent + UNKNOWN absence is consistent", not viol_ok)

    print("=== GATE: source=unknown is not proof ===")
    rec4 = evidence_store.open_evidence("gate_unknown_src")
    ref = False
    try:
        evidence_store.save_classify(rec4, {
            "verdict": "PASS",
            "provenance": {"source": "unknown", "sha256": "x",
                           "foreground": {"process": "Code.exe",
                                          "is_code": True}}})
    except env_proof.ProofRequired:
        ref = True
    check("PASS with source=unknown refused", ref)


# ---------------------------------------------------------------------------
# 2. mutation: break each protection, the cases must go red
# ---------------------------------------------------------------------------

def _mutate(attr_owner, attr_name, replacement):
    real = getattr(attr_owner, attr_name)
    setattr(attr_owner, attr_name, replacement)
    return real


def _restore(attr_owner, attr_name, real):
    setattr(attr_owner, attr_name, real)


def suite_mutation() -> None:
    print("=== MUTATION: each protection must be load-bearing ===")
    base = runner.run_contract(CONTRACT, record=False, verbose=False)
    check("baseline green (%d/%d)" % (base["n_passed"], base["n_cases"]),
          base["ok"])

    # M1: proof gate always says "fine"
    real = _mutate(env_proof, "proof_status", lambda payload: (True, []))
    try:
        m1 = runner.run_contract(CONTRACT, record=False, verbose=False)
    finally:
        _restore(env_proof, "proof_status", real)
    f1 = [r["case_key"].split(".")[-1] for r in m1["results"] if not r["passed"]]
    check("MUT1 proof gate neutered -> caught", bool(f1), str(f1))

    # M2: write gate accepts everything
    real_save = evidence_store.save_classify
    evidence_store.save_classify = (
        lambda rec, payload, *, enforce_proof=True:
        real_save(rec, payload, enforce_proof=False)
    )
    try:
        m2 = runner.run_contract(CONTRACT, record=False, verbose=False)
    finally:
        evidence_store.save_classify = real_save
    f2 = [r["case_key"].split(".")[-1] for r in m2["results"] if not r["passed"]]
    check("MUT2 write gate neutered -> caught", bool(f2), str(f2))

    # M3: classification rule engine returns "all good"
    real_cls = _mutate(env_proof, "corrected_classification",
                       lambda payload: ({"category": "geometry_fail",
                                         "verdict": "FAIL"}, []))
    try:
        m3 = runner.run_contract(CONTRACT, record=False, verbose=False)
    finally:
        _restore(env_proof, "corrected_classification", real_cls)
    f3 = [r["case_key"].split(".")[-1] for r in m3["results"] if not r["passed"]]
    check("MUT3 classification rule neutered -> caught", bool(f3), str(f3))

    # M4: demand proof for EVERYTHING, including UNKNOWN. The honest absence
    # outcome must then become unwritable, so the pass-side counterpart goes RED.
    real_req = _mutate(env_proof, "proof_required", lambda payload: True)
    try:
        m4 = runner.run_contract(CONTRACT, record=False, verbose=False)
    finally:
        _restore(env_proof, "proof_required", real_req)
    f4 = [r["case_key"].split(".")[-1] for r in m4["results"] if not r["passed"]]
    check("MUT4 proof demanded for UNKNOWN -> caught", bool(f4), str(f4))

    # M5: rule engine reports a violation for EVERYTHING. The consistent
    # container-present geometry case must then go RED.
    real_cls2 = _mutate(env_proof, "corrected_classification",
                        lambda payload: ({"category": "geometry_fail",
                                          "verdict": "FAIL"},
                                         ["always a violation"]))
    try:
        m5 = runner.run_contract(CONTRACT, record=False, verbose=False)
    finally:
        _restore(env_proof, "corrected_classification", real_cls2)
    f5 = [r["case_key"].split(".")[-1] for r in m5["results"] if not r["passed"]]
    check("MUT5 rule engine rejects everything -> caught", bool(f5), str(f5))


def main() -> int:
    suite_write_gate()
    print()
    suite_mutation()
    print()
    # Guard against the pollution that this suite used to cause: the real
    # evidence directory must have gained nothing. If it did, a code path
    # bypassed the redirected root.
    real_root = evidence_store.DEFAULT_EVIDENCE_ROOT
    if real_root.is_dir():
        offenders = [
            p.name for p in real_root.iterdir()
            if p.is_dir() and p.name.startswith(("EVID-gate_", "EVID-tdd_",
                                                 "EVID-probe"))
        ]
        check("no test noise written to the REAL evidence root",
              not offenders, str(offenders[:5]))
    check("test ran against an isolated root", not evidence_store.is_default_root())

    n = sum(1 for _, ok, _ in RESULTS if ok)
    print("=== %d/%d checks passed ===" % (n, len(RESULTS)))
    for name, ok, detail in RESULTS:
        if not ok:
            print("   FAILED:", name, detail)
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
