"""Guards for the multi-dimensional prompt SSOT.

Composition is the one place where a mistake is SILENT. If a slot is not
substituted, `{{dim:negation}}` reaches the model as literal text and the run
looks normal while the prompt is wrong. Every guard here exists because that
failure mode has no downstream symptom.

Each check isolates ONE protection, and the mutation block proves the checks
would notice if the protection were removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")
import prompt_dimension as pd  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print("  %s  %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("        %s" % detail)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except pd.DimensionError as e:
        return str(e)


def main() -> int:
    conn = pd._connect(r"C:\projects\agent_system\agent.db")
    SKILL = "mouse_spot_verify"
    print("=== test_prompt_dimension ===")

    pd.seed_mouse_spot(conn, SKILL)
    reg = pd.registry(conn, SKILL)
    print("  registry: %s" % {k: sorted(v["values"]) for k, v in reg.items()})
    check("4 axes registered", len(reg) == 4, "got %s" % sorted(reg))
    check("each axis has >=2 values",
          all(len(v["values"]) >= 2 for v in reg.values()))

    # ---- 1. happy path + determinism --------------------------------------
    vals = {"context": "plain", "criterion": "positive_only",
            "negation": "none", "output": "simple"}
    c1 = pd.compose_combo(conn, SKILL, vals, template=pd.MOUSE_SPOT_TEMPLATE)
    c2 = pd.compose_combo(conn, SKILL, dict(reversed(list(vals.items()))),
                          template=pd.MOUSE_SPOT_TEMPLATE)
    check("same values -> same sha (order-independent)", c1["sha256"] == c2["sha256"])
    check("same values -> same combo_key", c1["combo_key"] == c2["combo_key"])
    check("no literal slot left in output", "{{dim:" not in c1["prompt_text"])
    check("target_name placeholder preserved for the caller",
          "{{target_name}}" in c1["prompt_text"])

    # different value -> different prompt
    vals2 = dict(vals, negation="stack")
    c3 = pd.compose_combo(conn, SKILL, vals2, template=pd.MOUSE_SPOT_TEMPLATE)
    check("different value -> different sha", c1["sha256"] != c3["sha256"])
    check("different value -> different prompt_key",
          c1["prompt_key"] != c3["prompt_key"])

    # ---- 2. guards ---------------------------------------------------------
    e = raises(pd.compose_prompt, conn, SKILL, dict(vals, bogus="x"),
               template=pd.MOUSE_SPOT_TEMPLATE)
    check("unknown DIMENSION refused", e is not None and "unknown dimension" in e,
          "got %r" % e)

    e = raises(pd.compose_prompt, conn, SKILL, dict(vals, negation="not_a_value"),
               template=pd.MOUSE_SPOT_TEMPLATE)
    check("unknown VALUE refused", e is not None and "no value" in e,
          "got %r" % e)

    e = raises(pd.compose_prompt, conn, SKILL,
               {"context": "plain"}, template=pd.MOUSE_SPOT_TEMPLATE)
    check("missing slot value refused (would leave a literal placeholder)",
          e is not None and "needs dimension" in e, "got %r" % e)

    e = raises(pd.compose_prompt, conn, SKILL, vals, template="A fixed prompt.")
    check("template with NO slots refused (cannot be varied)",
          e is not None and "no {{dim:" in e, "got %r" % e)

    e = raises(pd.compose_prompt, conn, SKILL, vals,
               template="{{dim:context}} {{dim:not_registered}}")
    check("unregistered slot in template refused",
          e is not None and "not_registered" in (e or ""), "got %r" % e)

    # ---- 3. the whole space composes --------------------------------------
    combos = pd.expand_combos(conn, SKILL)
    check("36 combinations enumerated", len(combos) == 36, "got %d" % len(combos))
    shas, bad = set(), []
    for cv in combos:
        try:
            out = pd.compose_prompt(conn, SKILL, cv, template=pd.MOUSE_SPOT_TEMPLATE)
            if "{{dim:" in out:
                bad.append(pd.combo_key(cv))
            shas.add(out)
        except Exception as ex:  # noqa: BLE001
            bad.append("%s (%s)" % (pd.combo_key(cv), ex))
    check("every combination composes cleanly", not bad, "failed: %s" % bad[:3])
    print("  distinct prompts produced: %d of %d" % (len(shas), len(combos)))
    check("combinations produce >1 distinct prompt (the axis is real)",
          len(shas) > 1, "all combinations produced identical text")
    # The 'output' axis only changes the Result line; two combos differing ONLY
    # in output must still differ.
    only_out = {pd.compose_prompt(conn, SKILL, dict(vals, output=o),
                                 template=pd.MOUSE_SPOT_TEMPLATE)
                for o in ("simple", "with_unknown")}
    check("changing only the 'output' axis changes the prompt", len(only_out) == 2)

    # ---- 4. the axis values are actually different ------------------------
    neg_none = reg["negation"]["values"]["none"]["value_text"]
    neg_stack = reg["negation"]["values"]["stack"]["value_text"]
    check("negation:none is empty and negation:stack is long (axis is meaningful)",
          neg_none.strip() == "" and len(neg_stack) > 100,
          "none=%r len(stack)=%d" % (neg_none, len(neg_stack)))

    # ---- 5. mutation: does a neutered guard actually let damage through? ---
    # Simulate strict=False (the guard switched off) on a template with an
    # unregistered slot. If this did NOT leak a placeholder, the guard would be
    # decorative.
    leaked = pd.compose_prompt(conn, SKILL, vals,
                               template="{{dim:context}} {{dim:not_registered}}",
                               strict=False)
    check("MUTATION: with strict=False a literal slot DOES leak to the model",
          "{{dim:not_registered}}" in leaked,
          "expected the guard to be load-bearing; got %r" % leaked[:80])
    check("MUTATION: so strict=True is what prevents it (guard is load-bearing)",
          raises(pd.compose_prompt, conn, SKILL, vals,
                 template="{{dim:context}} {{dim:not_registered}}") is not None)

    conn.close()
    n_pass = sum(1 for _, p, _ in CHECKS if p)
    print()
    print("%d/%d checks passed" % (n_pass, len(CHECKS)))
    return 0 if n_pass == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
