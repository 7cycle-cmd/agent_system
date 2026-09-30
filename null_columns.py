# -*- coding: utf-8 -*-
"""null_columns.py — WHICH NULLs may become NA, and which must stay NULL.

THE DANGER THIS MODULE EXISTS TO PREVENT
----------------------------------------
A blind backfill would write `NA` into 15156 cells. But `NA` is a CLAIM: "this
field does not apply to this row". Measured, that claim is FALSE for most of them:

  * `skill_prompt_inference.human_override` 3341/3341 NULL
        -> "nobody has reviewed this yet" is an ABSENCE. Writing NA would claim
           every row was reviewed and found not-applicable. FABRICATION.
  * `audit_trace.failure_class` 189/2492 NULL
        -> those are the rows this workset could NOT classify. NA would claim
           "not applicable" when the truth is "not yet classified".
  * `llm_100_run.linked_trace_id` 2615/2615 NULL
        -> the NAME says it points at a trace. NULL means "no trace linked" — a
           DECISION. The DDL simply forgot to declare the FK.

So the module does three things, in this order:

  1. CLASSIFY every NULL column: VALUE / REFERENCE_BY_NAME / FK_DECLARED.
     A column whose name ends in an id/ref/key/hash token is a REFERENCE even
     when the DDL did not declare it, because the NAME is evidence of intent.

  2. REFUSE to write anything that has no DECISION. The decision map is explicit
     and per column, and an undecided column is REPORTED, never defaulted. A
     default is how an absence becomes a decision.

  3. FLAG the 100%-NULL columns. A field nobody has EVER filled is not "not
     applicable" — it is UNUSED, and writing NA there fabricates a decision for
     every row at once.

THE THREE DECISIONS
-------------------
    NA        the empty state is a PROPERTY OF THE ROW ("does not apply")
    KEEP_NULL the empty state is WORK NOT DONE, and must stay visible
    UNUSED    the column has never been filled; it is not a value axis at all
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import no_null as nn  # noqa: E402

DB = BASE / "agent.db"

# A column whose name ENDS in one of these points at another row. Anchored at the
# end on purpose: `parent_id` matches, `identity` does not. A loose pattern would
# classify half the schema as a reference and then refuse to standardise it.
RX_REF = re.compile(r"(_id|_ids|_ref|_refs|_key|_keys|_hash|_trace_id)$", re.I)

CAT_VALUE = "VALUE"
CAT_REFERENCE = "REFERENCE_BY_NAME"
CAT_FK = "FK_DECLARED"

DEC_NA = "NA"
DEC_KEEP = "KEEP_NULL"
DEC_UNUSED = "UNUSED"
DECISIONS = (DEC_NA, DEC_KEEP, DEC_UNUSED)

# ---------------------------------------------------------------------------
# THE DECISION MAP — per column, with the REASON. Nothing is defaulted.
#
# A column is listed here only when the empty state is UNAMBIGUOUSLY a property
# of the row. Everything else is deliberately absent, so the engine REFUSES it
# and the refusal is visible.
#
# The rule used to admit a column:
#   the column is a DETAIL OF A CONDITION, and the condition is recorded
#   elsewhere in the same row. "No failure" -> no failure_reason. "Not finished"
#   -> no finished_at. The absence is DERIVABLE from another column, so NA
#   states a fact rather than hiding one.
# ---------------------------------------------------------------------------
DECISION_MAP: dict[tuple[str, str], tuple[str, str]] = {
    # --- a detail of a condition recorded in the same row -------------------
    ("llm_100_run", "failure_reason"):
        (DEC_NA, "a run that did not fail has no failure reason; `win` records "
                 "the outcome, so the absence is derivable"),
    ("skill_mismatch_log", "error_reason"):
        (DEC_NA, "a mismatch with no error has no reason; `status` records the "
                 "outcome"),
    ("dev_task", "error"):
        (DEC_NA, "a task that did not fail has no error; `status` records it"),
    ("dev_task", "completed_at"):
        (DEC_NA, "an unfinished task has no completion time; `status` records it"),
    ("dev_task", "qc_summary"):
        (DEC_NA, "a task with no QC has no summary; `qc_vision_id` records it"),
    ("dev_task", "current_model"):
        (DEC_NA, "a task never run has no model; `status` records it"),
    ("dev_task", "writer"):
        (DEC_NA, "a task never run has no writer; `status` records it"),
    ("task_instances", "finished_at"):
        (DEC_NA, "an unfinished instance has no finish time; `status` records it"),
    ("task_instances", "trace_log"):
        (DEC_NA, "an instance with no trace has no log; `status` records it"),
    ("fault_event", "resolved_at"):
        (DEC_NA, "an unresolved event has no resolution time; `status` records it"),
    ("fault_report", "notified_at"):
        (DEC_NA, "an unreported fault has no notification time"),
    ("fn_request", "error_text"):
        (DEC_NA, "a request that did not fail has no error; `status` records it"),
    # --- a free-text note that simply may not exist -------------------------
    ("skill_prompt_case", "notes"):
        (DEC_NA, "a case may carry no note; the note is optional by design"),
    ("skill_prompt_case", "expected_reason"):
        (DEC_NA, "a case may carry no reason; `expected` records the verdict"),
    ("skill_contract_field", "remark"):
        (DEC_NA, "a field may carry no remark; `hard_rule` records the rule"),
    ("onto_link", "notes"):
        (DEC_NA, "a link may carry no note"),
    ("track_id_audit", "notes"):
        (DEC_NA, "an audit row may carry no note"),
    ("workflow_step", "notes"):
        (DEC_NA, "a step may carry no note"),
    ("db_table_registry", "description"):
        (DEC_NA, "a registry row may carry no description; `name` records it"),
    # --- a value that is genuinely optional ---------------------------------
    ("skill_contract_field", "rule_value_json"):
        (DEC_NA, "only a `const` rule carries a value; `rule_kind` records which"),
    ("dev_task_field", "default_text"):
        (DEC_NA, "a field with no default has none; `nullable` records it"),
    ("fault_solution", "steps_json"):
        (DEC_NA, "a solution may have no structured steps; `steps_text` records "
                 "the prose"),
    ("skill_template", "spec"):
        (DEC_NA, "a template may carry no spec"),
    ("user_asset", "version"):
        (DEC_NA, "an asset may carry no version"),
    ("source", "instruction_limit"):
        (DEC_NA, "a source may declare no instruction limit"),
    ("plan_session_log", "from_stage"):
        (DEC_NA, "the first stage has no previous stage; `stage` records it"),
    # --- DELIBERATELY ABSENT, and the engine will REFUSE them ---------------
    # ("skill_prompt_inference", "human_override")  -> 100% NULL, UNUSED
    # ("skill_prompt_inference", "confidence")      -> "not recorded", an absence
    # ("audit_trace", "failure_class")              -> "not yet classified"
    # ("audit_trace", "capability")                 -> "not yet classified"
    # ("skill_registry", "taxonomy_path")           -> MY OWN defect, see below
}


class UndecidedColumn(RuntimeError):
    """Raised when a column has no decision. A default would be a fabrication."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("no decision — refusing to write: %s"
                         % "; ".join(reasons))


def classify(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every column holding NULL, with its category and its decision."""
    out: list[dict[str, Any]] = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "ORDER BY name"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        if n == 0:
            continue
        fks = nn.fk_columns(conn, t)
        for c in conn.execute("PRAGMA table_info(%s)" % t):
            col, decl = c[1], (c[2] or "")
            try:
                k = conn.execute("SELECT COUNT(*) FROM %s WHERE %s IS NULL"
                                 % (t, col)).fetchone()[0]
            except sqlite3.Error:
                continue
            if not k:
                continue
            if col in fks:
                cat = CAT_FK
            elif RX_REF.search(col):
                cat = CAT_REFERENCE
            else:
                cat = CAT_VALUE
            dec, why = DECISION_MAP.get((t, col), ("", ""))
            if not dec:
                if cat != CAT_VALUE:
                    dec, why = DEC_KEEP, ("a reference: NULL means 'no "
                                          "reference', which is a decision")
                elif k == n:
                    dec, why = DEC_UNUSED, ("100%% NULL: nobody has ever filled "
                                            "this, so it is not a value axis")
                else:
                    dec, why = "", "UNDECIDED — needs a human decision"
            out.append({"table": t, "column": col, "declared": decl,
                        "nulls": k, "rows": n, "category": cat,
                        "decision": dec, "reason": why,
                        "kind": (nn.KIND_FK if cat == CAT_FK
                                 else nn.kind_of(decl))})
    out.sort(key=lambda d: -d["nulls"])
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter
    by_dec = Counter(r["decision"] or "UNDECIDED" for r in rows)
    nulls = Counter()
    for r in rows:
        nulls[r["decision"] or "UNDECIDED"] += r["nulls"]
    # The CATEGORY counts are reported too, because a category is what a
    # measurement reads: `REFERENCE_BY_NAME` is the set of columns whose NAME
    # promises a reference, so a NULL there is an ILLEGAL reference — a
    # different quantity from the decision counts, and not derivable from them.
    by_cat = Counter(r["category"] for r in rows)
    nulls_cat = Counter()
    for r in rows:
        nulls_cat[r["category"]] += r["nulls"]
    return {"columns": len(rows),
            "by_decision": dict(by_dec),
            "nulls_by_decision": dict(nulls),
            "by_category": dict(by_cat),
            "nulls_by_category": dict(nulls_cat),
            "writable_nulls": nulls.get(DEC_NA, 0),
            "undecided_columns": [{"table": r["table"], "column": r["column"],
                                   "nulls": r["nulls"]}
                                  for r in rows if not r["decision"]][:20]}


def backfill(conn: sqlite3.Connection, *, apply: bool = False,
             only: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    """Write NA ONLY where a decision says NA. Everything else is REFUSED.

    `only` restricts the run to named columns, so a backfill can be done one
    column at a time with its own proof.
    """
    rows = classify(conn)
    written: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for r in rows:
        key = (r["table"], r["column"])
        if only and key not in only:
            continue
        if r["decision"] != DEC_NA:
            refused.append({"table": r["table"], "column": r["column"],
                            "nulls": r["nulls"],
                            "decision": r["decision"] or "UNDECIDED",
                            "reason": r["reason"]})
            continue
        if r["category"] != CAT_VALUE:
            refused.append({"table": r["table"], "column": r["column"],
                            "nulls": r["nulls"], "decision": r["decision"],
                            "reason": "not a VALUE column (%s)" % r["category"]})
            continue
        na = nn.na_value(r["kind"])
        before = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE %s IS NULL" % (r["table"], r["column"])
        ).fetchone()[0]
        if apply:
            # Only NULLs are touched. A real value is NEVER rewritten — that is
            # the difference between standardising and fabricating.
            conn.execute("UPDATE %s SET %s = ? WHERE %s IS NULL"
                         % (r["table"], r["column"], r["column"]), (na,))
        after = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE %s IS NULL" % (r["table"], r["column"])
        ).fetchone()[0]
        written.append({"table": r["table"], "column": r["column"],
                        "kind": r["kind"], "na": na,
                        "nulls_before": before, "nulls_after": after,
                        "reason": r["reason"]})
    if apply:
        conn.commit()
    return {"applied": apply, "written": written, "refused": refused,
            "written_columns": len(written),
            "refused_columns": len(refused),
            "nulls_standardised": sum(w["nulls_before"] - w["nulls_after"]
                                      for w in written)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--classify", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.classify:
            rows = classify(conn)
            print(json.dumps(summary(rows), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(backfill(conn, apply=args.apply),
                             indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()