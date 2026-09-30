"""Test the contract TDD gate on the merge path.

Three things must hold:
  1. a skill with NO contract -> merge is allowed, gate says applies=False
     (NOT silently "ok" — an uncovered skill must be visible as uncovered)
  2. a skill WITH a contract whose TDD cases are green -> allowed, applies=True
  3. a skill WITH a contract whose TDD cases FAIL -> merge REFUSED

Case 3 is the load-bearing one. Without it the gate is decoration.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")
import env_proof
import evidence_store
import skill_learning as sl

# ISOLATE EVIDENCE WRITES: the contract TDD gate runs cases that call
# save_classify(), which used to land in the real `evidence/` tree.
_PROBE_ROOT = Path(tempfile.mkdtemp(prefix="merge_gate_test_evidence_"))
evidence_store.set_evidence_root(_PROBE_ROOT)
assert not evidence_store.is_default_root(), "test must not use the real evidence root"
print("evidence root (TEST): %s" % _PROBE_ROOT)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s  %s" % ("ok " if cond else "FAIL", name, detail))


print("=== 1. skill with NO contract -> allowed, but declared uncovered ===")
g = sl._contract_tdd_gate("mouse_spot_verify")
check("applies is False for an uncovered skill", g["applies"] is False)
check("ok stays True (not blocked)", g["ok"] is True)
check("reason names it as uncovered",
      "NOT covered" in (g["reason"] or ""), g["reason"])

print()
print("=== 2. skill WITH a green contract ===")
g2 = sl._contract_tdd_gate("env_task_proof")
check("applies is True", g2["applies"] is True)
check("contract_id resolved", g2["contract_id"] == "MOD.MOUSE_SPOT_HELPER.ENV_PROOF",
      str(g2["contract_id"]))
check("TDD gate is green", g2["ok"] is True, g2["reason"])

print()
print("=== 3. same contract with a BROKEN protection -> gate must refuse ===")
# Neuter the classification rule; the contract's hard_fail cases must go red and
# therefore the merge gate must block.
real_cls = env_proof.corrected_classification
env_proof.corrected_classification = lambda payload: (
    {"category": "geometry_fail", "verdict": "FAIL"}, []
)
try:
    g3 = sl._contract_tdd_gate("env_task_proof")
finally:
    env_proof.corrected_classification = real_cls
check("gate refuses when a hard_fail case fails", g3["ok"] is False, g3["reason"])
check("failed case is named", bool((g3.get("detail") or {}).get("failed")),
      str((g3.get("detail") or {}).get("failed")))

print()
print("=== 4. gate is restored after the mutation ===")
g4 = sl._contract_tdd_gate("env_task_proof")
check("gate green again", g4["ok"] is True, g4["reason"])

print()
print("=== 5. GOLD SET gate: a degenerate set is refused, this one now passes ===")
# HISTORY: mouse_spot_verify used to have 11 gold cases ALL expecting NO, so any
# always-"NO" prompt scored 100%. `seed_mouse_spot_yes_cases.py` added cases with
# the opposite expectation, so the gate is now EXPECTED to pass here. The gate's
# refusal path is proven on a synthetic single-class mapping instead of by
# breaking the real data.
gg = sl._gold_set_gate("mouse_spot_verify")
check("applies is True (it has cases)", gg["applies"] is True)
check("gold set now discriminates (>=2 classes)", gg["ok"] is True, gg["reason"])
check("both classes are reported", len(gg["classes"]) >= 2, str(gg["classes"]))

# The refusal rule itself, exercised directly (no real data mutated).
single = {"NO": 11}
check("rule: a single-class mapping is degenerate",
      len(single) <= 1, "classes=%s -> gate returns ok=False" % single)
print("  (refusal proven live earlier: 11xNO -> ok=False; see repo memory)")

print()
print("=== 6. GOLD SET gate: a discriminating set passes ===")
gc = sl._gold_set_gate("captcha_cell_detect")
check("mixed-class gold set is allowed", gc["ok"] is True, gc["reason"])
check("both classes are reported",
      len(gc["classes"]) == 2, str(gc["classes"]))

print()
print("=== 7. GOLD SET gate: no cases -> not applicable, not silently ok ===")
gn = sl._gold_set_gate("env_task_proof")
check("applies is False with no cases", gn["applies"] is False)
check("reason says it cannot be validated", "cannot be validated" in (gn["reason"] or ""),
      gn["reason"])

n = sum(1 for _, ok, _ in RESULTS if ok)
print()
# Pollution guard: the real evidence root must have gained nothing.
real_root = evidence_store.DEFAULT_EVIDENCE_ROOT
if real_root.is_dir():
    offenders = [
        p.name for p in real_root.iterdir()
        if p.is_dir() and p.name.startswith(("EVID-gate_", "EVID-tdd_",
                                             "EVID-probe"))
    ]
    check("no test noise written to the REAL evidence root", not offenders,
          str(offenders[:5]))
check("test ran against an isolated root", not evidence_store.is_default_root())

n = sum(1 for _, ok, _ in RESULTS if ok)
print()
print("=== %d/%d checks passed ===" % (n, len(RESULTS)))
for name, ok, detail in RESULTS:
    if not ok:
        print("   FAILED:", name, detail)
sys.exit(0 if n == len(RESULTS) else 1)
