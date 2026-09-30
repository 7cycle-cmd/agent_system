# -*- coding: utf-8 -*-
"""discover_cases.py — steps 5 and 6 of the loop: DISCOVER and STOP.

WHAT EXISTS, MEASURED (so nothing here is speculative)
------------------------------------------------------
STEP 1 PROOF  : `skill_contract_store.streak_evidence()` already splits
                evidence / counting_only / below_target. Live: 13 evidence,
                4 counting_only, 1 below_target.
STEP 4 PROOF  : `record_streak_result` counts DISTINCT CODE STATES, not runs.
STEP 5 DISCOVER: NOTHING consumes a proof and produces a case. Every writer of
                `skill_contract_tdd_case` is a hand-written `_register_*` script
                (grep: 14 writers, 0 of them driven by a proof result).
STEP 6 STOP   : does not exist.

THE ONE JUDGEMENT THIS MODULE REFUSES TO MAKE
---------------------------------------------
"Are these two cases similar?" would be a similarity SCORE, and `independent_review`
already forbids that: measure AMBIGUITY, not OVERLAP. So this module never scores
similarity. It asks the only question a rule can decide:

    given a PROVEN contract, which OTHER contracts does its rule family match,
    with ONE candidate (decided) or SEVERAL (ambiguous)?

That is the same judgement-free discriminator `binding_proposals.confirmable_groups`
already uses, reused rather than reinvented. A family with ONE member is a
CONFIRMED EXPANSION. A family with SEVERAL is AMBIGUOUS and is reported as such,
never resolved by picking the biggest.

THE RULE FAMILY IS READ FROM THE SOURCE, NOT TYPED HERE
------------------------------------------------------
A TDD case carries an `assertion` string. The family is the module path its
validation comes from, e.g. `register:field_tdd_rule:<n>` -> `field_tdd_rule`.
Reading it from the stored value keeps this module from inventing a taxonomy.

WHY `--forever` DOES NOT EXIST
------------------------------
`run_forever()` is deliberately absent. The loop is safe BECAUSE each pass yields
to a human-shaped gate; an unattended `while True` would either spin on a
saturated family or invite someone to loosen a gate to keep it turning. So there
is `pass_once()` and `stop_reason()`, and the STOP is a MEASUREMENT (a pass that
found a new distinct state), not a counter.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The threshold is NOT defined here. It comes from `streak_evidence`, which reads
# `target_streak` per contract. Hard-coding 20/100 in this file would create a
# second answer to a question the store already answers.

# ---------------------------------------------------------------------------
# FAMILY, READ FROM THE SCHEMA RATHER THAN GUESSED
# ---------------------------------------------------------------------------
# FIRST VERSION WAS BLIND, AND ITS BLINDNESS LOOKED LIKE A RESULT.
#
# It derived the family from the `assertion` text with a regex for
# `register:`/`file.py:` citations, and reported ONE family — `UNKNOWN` — holding
# all 105 cases. A single uniform answer is the signature of an extractor that
# cannot see, exactly like the two earlier blindness defects in this session.
# Measured: `assertion` is PROSE ('Chat Center call, skip_log=True -> hash
# generated, no DB write'). It carries no source reference at all, so the regex
# could never match. `skill_contract_tdd_case` has no source column either.
#
# The family IS available and is SCHEMA-DEFINED: `contract_id` starts with the
# entity-id LETTER, and those letters are assigned by `entity_registry`
# (`entity_type_registry`), not by this module. Reading the prefix keeps the
# taxonomy in ONE place.
_RX_CONTRACT_FAMILY = re.compile(r"^([A-Z]+)(?:-|[.])")


class DiscoverError(ValueError):
    """Raised when a discovery would be a judgement rather than a measurement."""


def family_of(contract_id: str) -> str:
    """The entity-type family of a contract, from its own id.

    `SKILL-0001` -> SKILL, `CAP.PPT.PRODUCE` -> CAP, `CH.LOCAL_PC` -> CH.
    An id that matches nothing is UNKNOWN, never a guessed family: inventing one
    would create the silent default this project keeps removing.
    """
    s = str(contract_id or "").strip()
    m = _RX_CONTRACT_FAMILY.match(s)
    if m:
        return m.group(1)
    return "UNKNOWN"


def proven_contracts(conn: sqlite3.Connection) -> dict[str, Any]:
    """Contracts whose streak is EVIDENCE. Reads the store's own verdict."""
    import skill_contract_store as scs

    cids = [r[0] for r in conn.execute(
        "SELECT DISTINCT contract_id FROM skill_contract_tdd_case")]
    proven, not_proven, detail = [], [], {}
    for cid in cids:
        ev = scs.streak_evidence(cid, conn=conn)
        # `streak_evidence` returns only {ok, state, dedicated_probe, proves}
        # when there is NO streak row (`state='no_streak'`) — measured, not
        # assumed. Reading `ev["best_streak"]` raised KeyError on a fresh DB.
        # A MISSING count is reported as None, never coerced to 0: "no run
        # recorded" and "zero wins" are different facts.
        detail[cid] = {
            "state": ev["state"],
            "best": ev.get("best_streak"),
            "target": ev.get("target_streak"),
            "current": ev.get("current_streak"),
            "dedicated_probe": ev["dedicated_probe"],
            "proves": ev["proves"],
        }
        (proven if ev["state"] == "evidence" else not_proven).append(cid)
    return {"proven": proven, "not_proven": not_proven, "detail": detail,
            "why": ("`evidence` means the streak advanced over DISTINCT CODE "
                    "STATES, so it is not a repetition count")}


def discover(conn: sqlite3.Connection, **kw) -> dict[str, Any]:
    """Which siblings could be proven NEXT, and which the rule cannot decide.

    NEVER scores similarity. The discriminator is the NUMBER of candidate
    families: one -> confirmable expansion; several -> ambiguous, reported.
    """
    fams: dict[str, list[str]] = {}
    for r in conn.execute(
            "SELECT DISTINCT case_key, contract_id FROM skill_contract_tdd_case"):
        f = family_of(r["contract_id"])
        fams.setdefault(f, []).append(r["contract_id"])

    pf = proven_contracts(conn)
    proven_set = set(pf["proven"])
    confirmable, ambiguous, unknown, fully_proven = [], [], [], []
    for fam, cids in sorted(fams.items()):
        unnext = sorted(set(cids) - proven_set)
        rec = {"family": fam, "contracts": sorted(set(cids)),
               "proven": sorted(set(cids) & proven_set),
               "next_candidates": unnext,
               "candidate_count": len(unnext)}
        if fam == "UNKNOWN":
            unknown.append(rec)
        elif len(unnext) == 1:
            confirmable.append(rec)
        elif len(unnext) > 1:
            ambiguous.append(rec)
        else:
            # A FOURTH BUCKET, added because the proof's own accounting check
            # failed: 8 families were found but 5+0+0 = 5, so three families
            # belonged to NO bucket. They are the ones whose every member is
            # already proven — an outcome, not an omission.
            fully_proven.append(rec)
    return {
        "families": len(fams),
        "confirmable": confirmable,
        "ambiguous": ambiguous,
        "unknown_family": unknown,
        "fully_proven": fully_proven,
        "confirmable_count": len(confirmable),
        "ambiguous_count": len(ambiguous),
        "unknown_count": len(unknown),
        "fully_proven_count": len(fully_proven),
        "note": ("`confirmable` means the FAMILY decided (exactly one unproven "
                 "member). It does not mean the case is worth writing. A family "
                 "with several unproven members is AMBIGUOUS and is reported, "
                 "not resolved by picking the largest."),
    }


def stop_reason(conn: sqlite3.Connection, *, pass_result: dict | None = None
                ) -> dict[str, Any]:
    """Should the loop stop? Decided by STATE, never by a run counter.

    Stopping is a SATURATION outcome, not a failure. The reason is written down
    so a future reader cannot mistake saturation for an unfinished job.
    """
    d = discover(conn)
    if d["confirmable_count"] == 0 and d["ambiguous_count"] == 0:
        return {"stop": True, "reason": "SATURATED",
                "why": ("no family has an unproven member left: every case that "
                        "can be reached from a proven contract has been reached")}
    # UNSATURABLE: a confirmable family whose members cannot become `evidence`
    # however often they run. Measured cause: reaching `evidence` requires a
    # DEDICATED probe (`skill_contract_store.py:733-737`), and a contract whose
    # cases all resolve to the self-referential `_probe_payload_rule` fallback
    # can never get one. Repeating passes cannot help, so this is a STOP with a
    # distinct reason — NOT a prompt to loosen the probe rule.
    unsaturable = [r["next_candidates"][0] for r in d["confirmable"]
                   if r["next_candidates"]
                   and r.get("proven") == []]
    if unsaturable and pass_result is not None \
            and pass_result.get("new_code_state") is False:
        return {"stop": True, "reason": "UNSATURABLE",
                "contracts": unsaturable,
                "why": ("these contracts stay confirmable because no amount of "
                        "running can make them `evidence`; the missing input is "
                        "a DEDICATED probe, which is a code change, not a "
                        "iteration")}
    if pass_result is not None and pass_result.get("new_code_state") is False:
        return {"stop": True, "reason": "NO_NEW_STATE",
                "why": ("the pass produced no distinct code state, so further "
                        "passes would be counting, not proving")}
    return {"stop": False, "reason": "WORK_REMAINS",
            "why": "%d confirmable, %d ambiguous" % (d["confirmable_count"],
                                                     d["ambiguous_count"])}


def assert_not_loosened(*, gate: str, before: Any, after: Any) -> None:
    """REFUSE a gate change that exists only to keep the loop turning.

    The rule, stated so it can be enforced rather than remembered:

        a gate may be changed by a MEASUREMENT, never by a DESIRE TO CONTINUE.

    Called by any future pass that is tempted to widen a threshold after a
    saturation stop. Raising here is the intended behaviour.
    """
    raise DiscoverError(
        "gate %r must not be loosened to continue. before=%r after=%r. A "
        "saturation stop is a RESULT to report, not an obstacle to widen "
        "around." % (gate, before, after))


def pass_once(conn: sqlite3.Connection) -> dict[str, Any]:
    """One loop iteration: propose the next case, and REPORT the gate status.

    It does NOT write a case. Writing requires the case to be DERIVED and
    corroborated, which is a separate decision with its own gate; this pass
    produces the CANDIDATE list and the STOP verdict.
    """
    d = discover(conn)
    st = stop_reason(conn)
    return {
        "iteration": 1,
        "confirmable": d["confirmable"],
        "ambiguous": d["ambiguous"],
        "unknown_family": d["unknown_family"],
        "stop": st,
        "wrote_cases": 0,
        "why_no_write": ("a candidate is not a case. Writing needs a derived, "
                         "corroborated assertion, which is a different gate."),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--pass", dest="do_pass", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        out: dict[str, Any] = {}
        if args.discover:
            out["discover"] = discover(conn)
            out["proven"] = proven_contracts(conn)
        if args.do_pass:
            out["pass"] = pass_once(conn)
        if args.stop:
            out["stop"] = stop_reason(conn)
        if not out:
            out["discover"] = discover(conn)
            out["stop"] = stop_reason(conn)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()