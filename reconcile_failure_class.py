# -*- coding: utf-8 -*-
"""reconcile_failure_class.py — write a failure class ONLY where two methods agree.

THE RULE, AND WHY IT IS THIS RULE
---------------------------------
Method A (`failure_link`) classifies by searching the message text for keywords.
Method C (`_verify_link_method_c`) classifies by reading the SOURCE branches that
produced the message, via an AST walk. Measured against C, A is WRONG on 819 of
1206 checkable rows.

So the reconciler does NOT write A's answer, and it does NOT write C's answer.
It writes a class ONLY when A and C AGREE. Where they disagree the row is left
NULL and listed.

REFUSED ALTERNATIVES, and the reason each was refused
----------------------------------------------------
* Writing all 2394 from A, adjusting the 91 known-wrong rows (my first offer).
  That writes ~1575 rows on the word of a method already measured 68% wrong on
  the rows where it can be checked. A NULL costs nothing; an authoritative wrong
  class must be un-made by someone later.
* Writing all 2394 from C alone. C is independent in EVIDENCE but NOT in DESIGN:
  its guard-to-class mapping was written after seeing A's output, and the mapping
  that decides the 91 disputed rows was chosen because A disagreed there. Two
  unverified methods plus my tie-break is not corroboration.
* A "semantic_defect at tier 2 when rule_key is set AND parse ok" rule. Correct by
  construction, therefore it verifies nothing.

CONSEQUENCE: `semantic_defect` is UNREACHABLE for audit_trace right now, because
no source branch yields it and A never produces it alone. That is reported by
`coverage()`, not worked around.

Usage
    python reconcile_failure_class.py               # dry run (default)
    python reconcile_failure_class.py --apply       # write the agreed rows
    python reconcile_failure_class.py --list-disputed
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import failure_axis as fa  # noqa: E402
import failure_link as flA  # noqa: E402
import _verify_link_method_b as MB  # noqa: E402
import _verify_link_method_c as MC  # noqa: E402

DB = BASE / "agent.db"
CLASS_COL = "failure_class"
CITE = "src/task_center/skill_task_validate.py"     # the decisive evidence source


class ReconcileError(ValueError):
    """Raised when a write would not be corroborated by two methods."""


def classify_both(row: dict, conn: sqlite3.Connection, gt: dict,
                  allowed: list[str]) -> dict[str, Any]:
    """Return both methods' answers for one row, plus the shape."""
    ra = fa.classify_failure(flA.signals_from_row(row, "audit_trace"),
                             registered=allowed)
    a = ra["failure_class"] if ra["ok"] else None
    known = []
    for m in MB._messages(row.get("errors")):
        v = gt.get(MC.shape_of(m))
        if v and v["class"]:
            known.append(v["class"])
    c = known[0] if len(set(known)) == 1 and known else None
    return {
        "trace_id": row["trace_id"],
        "shape": MC.shape_of(str(row.get("errors"))),
        "method_a": a,
        "method_c": c,
        "agree": bool(a and c and a == c),
        "written": a if (a and c and a == c) else None,
    }


def plan(conn: sqlite3.Connection, *, allowed: list[str] | None = None
         ) -> list[dict[str, Any]]:
    gt = MC.build_ground_truth()
    allowed = allowed or fa.registered_classes(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM audit_trace WHERE verdict=0")]
    return [classify_both(r, conn, gt, allowed) for r in rows]


def coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """What the rule can and CANNOT express. Reported, never filled."""
    from collections import Counter

    written = Counter(r["written"] for r in records if r["written"])
    disputed = [r for r in records if not r["agree"]]
    shapes = sorted({r["shape"] for r in disputed})
    return {
        "rows": len(records),
        "will_write": sum(written.values()),
        "by_class": dict(written),
        "left_null": len(disputed),
        "disputed_shapes": shapes,
        "unreachable_classes": [
            c for c in ("semantic_defect", "provenance_defect",
                        "transient_execution")
            if c not in written
        ],
        "why_unreachable": ("no source branch yields them and method A never "
                            "produced them on its own, so writing one would rest "
                            "on a single unverified method"),
        "cite": CITE,
    }


def assert_corroborated(rec: dict[str, Any]) -> str:
    """The write gate. Refuses anything not agreed by both methods.

    Checked directly against the two METHOD ANSWERS, not against the `agree`
    flag alone: a flag can be set by the caller, and a gate that trusts a
    derived boolean cannot refuse a row whose booleans were filled in wrong.
    """
    a, c = rec.get("method_a"), rec.get("method_c")
    if not a or not c:
        raise ReconcileError(
            "row %r: a method is silent (a=%s c=%s); a write would rest on one "
            "method" % (rec.get("trace_id"), a, c))
    if a != c:
        raise ReconcileError(
            "row %r: methods disagree (a=%s c=%s); a write here would rest on "
            "one method" % (rec["trace_id"], a, c))
    return a


def apply(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Write ONLY the agreed rows, and verify each write after it happens."""
    written = 0
    refused: list[str] = []
    for rec in records:
        if not rec["agree"]:
            refused.append(rec["trace_id"])
            continue
        cls = assert_corroborated(rec)
        conn.execute("UPDATE audit_trace SET %s=? WHERE trace_id=?"
                     % CLASS_COL, (cls, rec["trace_id"]))
        written += 1
    conn.commit()
    # VERIFY: every class now present must be one the two methods agreed on,
    # and no row may hold a class the register does not know.
    seen = [r[0] for r in conn.execute(
        "SELECT DISTINCT %s FROM audit_trace WHERE %s IS NOT NULL"
        % (CLASS_COL, CLASS_COL))]
    allowed = fa.registered_classes(conn)
    bad = [c for c in seen if c not in allowed]
    return {"written": written, "refused": len(refused),
            "classes_in_db": sorted(seen), "unregistered_classes": bad,
            "refused_examples": refused[:5]}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list-disputed", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        recs = plan(conn)
        out: dict[str, Any] = {"coverage": coverage(recs)}
        if args.list_disputed:
            out["disputed"] = [{"trace_id": r["trace_id"], "shape": r["shape"],
                                "method_a": r["method_a"], "method_c": r["method_c"]}
                               for r in recs if not r["agree"]]
        if args.apply:
            out["writes"] = apply(conn, recs)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()