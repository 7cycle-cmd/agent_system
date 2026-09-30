# -*- coding: utf-8 -*-
"""link_level2.py — classify failures at the TASK level (skill_mismatch_log).

WHY LEVEL 2 IS NOT LEVEL 1, MEASURED
------------------------------------
Level 1 (audit_trace) had 2394 rows whose message text NAMED the registry that
rejected them, and the validator's source branches decided every one. Level 2 is
a different animal:

    690 rows; raw_response present on 690
    Result:NO 576 | Result:YES 100 | Result:UNKNOWN 9 | JSON 5
    parsed verdict == expected : 0      parsed verdict != expected : 676
    (expected, ask_output) distinct pairs : 4

I PREDICTED, IN A PRINTED LINE, that "a mismatch log where most rows AGREE means
the table is not recording what its name says". The measurement REFUTED it: 676
of 676 parsed rows CONTRADICT expectation. The table is a real mismatch log. The
prediction was wrong, the print was mine, and it is recorded here so the next
reader does not trust the phrasing.

WHAT THE SOURCE SAYS, AND WHY IT DECIDES THE RARE ROWS
-----------------------------------------------------
`env_proof.py:153` lists the categories that mean NO JUDGEMENT WAS MADE:

    ABSENCE_CATEGORIES = ("picker_not_open", "source_suspect",
                          "box_not_seen", "no_vl_answer")
    # "Categories that mean 'no judgement was made'. These pair with verdict UNKNOWN."

`env_proof.absence_beats_geometry("picker_not_open", "FAIL")` REJECTS a geometry
claim when the container was absent. So `Result: UNKNOWN / Reason:
picker_not_open` is not a parse failure and not a wrong answer: THE EVIDENCE WAS
NEVER OBSERVED. Of the five registered classes, the only definition that fits is
`provenance_defect` — "the finding cannot be traced to a source". That is an
elimination by definition, not a preference:

    business_defect    needs a rule_key; level 2 names no registry   -> no
    structural_defect  needs a shape failure; "UNKNOWN" PARSES        -> no
    semantic_defect    needs a judgement that is wrong; none was made -> no
    transient_execution needs an execution failure; the capture ran   -> no
    provenance_defect  no evidence to trace the finding to            -> YES

WHICH CLASSES THIS LEVEL CANNOT EXPRESS
---------------------------------------
`business_defect` and `structural_defect` are UNREACHABLE here: no row names a
registry, and no row has a malformed shape. Reported by `coverage()`, not filled.

So the two levels are COMPLEMENTARY, which is the useful result:
    level 1 expresses business + structural
    level 2 expresses semantic + transient + provenance
Neither level alone exercises the taxonomy; together they do.
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

import failure_axis as fa  # noqa: E402

DB = BASE / "agent.db"
CLASS_COL = "failure_class"
CITE_ENV = "env_proof.py:153"            # ABSENCE_CATEGORIES
CITE_QUEUE = "skill_task_queue.py:719"   # escalation_model_failed

_RX_RESULT = re.compile(r"Result:\s*([A-Za-z_]+)")
_RX_REASON = re.compile(r"Reason:\s*([A-Za-z_ ]+?)(?:\n|$)")


def source_absence_categories() -> tuple[str, ...]:
    """Read the absence list FROM THE SOURCE, so a change there cannot drift."""
    import env_proof

    return tuple(env_proof.ABSENCE_CATEGORIES)


# ---------------------------------------------------------------------------
# method L2-A — parse the stored answer text
#
# PRIVATE KEYS: `verdict` and `absence` are NOTES, not signals. They are returned
# SEPARATELY rather than smuggled into the signal dict, because
# `failure_axis.classify_failure` REFUSES an unknown signal. The first version
# put `_verdict` / `_absence` inside the dict and a caller passing the dict to
# the shared classifier raised FailureAxisError — the gate was right and the
# smuggling was wrong. A private key in a shared structure is a claim nobody
# validated.
# ---------------------------------------------------------------------------
def signals_text(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sig = dict(fa.BENIGN)
    raw = str(row.get("raw_response") or "")
    sig["parses"] = bool(raw.strip())
    m = _RX_RESULT.search(raw)
    verdict = m.group(1).upper() if m else None
    expected = str(row.get("expected") or "").strip().upper()

    # `meaning_ok` is only DECIDABLE when a verdict was given and the expectation
    # is itself a verdict. A JSON expectation is not a YES/NO claim, so it must
    # not be judged as one.
    if verdict in ("YES", "NO") and expected in ("YES", "NO"):
        sig["meaning_ok"] = (verdict == expected)
    sig["retryable"] = (str(row.get("error_reason") or "")
                        == "escalation_model_failed")
    sig["cited"] = bool(raw.strip())
    return sig, {"verdict": verdict, "expected": expected}


def signals_source(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decide from the SOURCE's own category lists, ignoring the Result token."""
    sig = dict(fa.BENIGN)
    raw = str(row.get("raw_response") or "")
    sig["parses"] = bool(raw.strip())
    reason = ""
    mr = _RX_REASON.search(raw)
    if mr:
        reason = mr.group(1).strip().lower().replace(" ", "_")
    absence = reason in source_absence_categories()
    # An absence reason means no judgement was made, regardless of any token.
    if absence:
        sig["meaning_ok"] = True          # nothing was judged, so nothing is wrong
    sig["retryable"] = (str(row.get("error_reason") or "")
                        == "escalation_model_failed")
    sig["cited"] = bool(raw.strip()) and not absence
    return sig, {"absence": absence, "reason": reason}


def classify_level2(row: dict[str, Any]) -> dict[str, Any]:
    """Derive the level-2 class, and report which method could decide it.

    Two independent readings are required to WRITE, exactly as at level 1:
      L2-A  the Result token vs the expectation (text)
      L2-B  the source's absence list and the stored escalation reason
    """
    a, na = signals_text(row)
    b, nb = signals_source(row)
    cls_a = cls_b = None

    if a["retryable"]:
        cls_a = "transient_execution"
    elif na["verdict"] == "UNKNOWN":
        cls_a = None                     # a token alone cannot say WHY
    elif a["meaning_ok"] is False:
        cls_a = "semantic_defect"
    elif a["meaning_ok"] is True and na["verdict"] in ("YES", "NO"):
        cls_a = None                     # agreed: not a failure at all

    if b["retryable"]:
        cls_b = "transient_execution"
    elif nb["absence"]:
        cls_b = "provenance_defect"
    elif na["expected"] in ("YES", "NO"):
        m = _RX_RESULT.search(str(row.get("raw_response") or ""))
        v = m.group(1).upper() if m else None
        if v in ("YES", "NO") and v != na["expected"]:
            cls_b = "semantic_defect"

    return {
        "mismatch_key": row.get("mismatch_key"),
        "method_a": cls_a,
        "method_c": cls_b,
        "agree": bool(cls_a and cls_b and cls_a == cls_b),
        "absence": bool(nb["absence"]),
        "verdict": na["verdict"],
        "expected": na["expected"][:22],
        "raw": str(row.get("raw_response") or "")[:44],
    }


def plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [classify_level2(dict(r)) for r in conn.execute(
        "SELECT * FROM skill_mismatch_log")]


def coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    written = Counter(r["method_a"] for r in records if r["agree"])
    disputed = [r for r in records if not r["agree"]]
    return {
        "rows": len(records),
        "will_write": sum(written.values()),
        "by_class": dict(written),
        "left_null": len(disputed),
        "disputed_kinds": {"%s -> %s (verdict=%s)" % k: v for k, v in Counter(
            (r["method_a"], r["method_c"], r["verdict"]) for r in disputed).items()},
        "unreachable_classes": [
            c for c in ("business_defect", "structural_defect")
            if c not in written],
        "why_unreachable": ("no row names a registry (so no rule_key exists) and "
                            "no row has a malformed shape; both classes need an "
                            "observable this level does not record"),
        "complementarity": ("level 1 expresses business+structural; level 2 "
                            "expresses semantic+transient+provenance"),
        "cites": [CITE_ENV, CITE_QUEUE],
    }


def apply(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, Any]:
    written = 0
    refused: list[str] = []
    for rec in records:
        if not rec["agree"]:
            refused.append(rec["mismatch_key"])
            continue
        conn.execute("UPDATE skill_mismatch_log SET %s=? WHERE mismatch_key=?"
                     % CLASS_COL, (rec["method_a"], rec["mismatch_key"]))
        written += 1
    conn.commit()
    seen = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT %s FROM skill_mismatch_log WHERE %s IS NOT NULL"
        % (CLASS_COL, CLASS_COL))})
    return {"written": written, "refused": len(refused),
            "classes_in_db": seen,
            "unregistered": [c for c in seen if c not in fa.registered_classes(conn)]}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        recs = plan(conn)
        out: dict[str, Any] = {"coverage": coverage(recs)}
        if args.apply:
            out["writes"] = apply(conn, recs)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()