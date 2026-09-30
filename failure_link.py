# -*- coding: utf-8 -*-
"""failure_link.py — carry a failure CLASS up the registry levels.

THE GAP, MEASURED BEFORE ANY CODE (static evidence, not an assumption)
---------------------------------------------------------------------
The capability-level failure record cannot state how it failed, and NOT by
omission — the schema forbids it:

    init_ontology_registry.sql:186
        verdict INTEGER NOT NULL DEFAULT 0 CHECK (verdict IN (0, 1))

So `audit_trace` holds ONE BIT: 2394 of 2492 rows are failures that cannot be
told apart from each other.

DO NOT WIDEN `verdict`. `CHECK (verdict IN (0, 1))` encodes a real invariant
worth keeping. A pass/fail BIT and a failure CLASS are two different facts, and
conflating them is how the bit stops meaning anything. The class is a SEPARATE
nullable column with the invariant stated in BOTH directions:

    verdict = FAIL  ->  failure_class IS NOT NULL   (a failure states its class)
    verdict = PASS  ->  failure_class IS NULL       (a pass has no class)

THE CLASS FIELD ALREADY EXISTS ONE LEVEL DOWN, AND IS EMPTY
-----------------------------------------------------------
`db_schema.py:1598` gives `skill_mismatch_log.error_reason TEXT` — the task-level
class column. It is NULL on 685 of 690 rows. So "we need a column" is not the
missing step; DERIVING is.

AND THE EXISTING CLASS IS COPIED, NOT CHOSEN
--------------------------------------------
`pair_qc.py:552` (and repeated at 613/635/649/663/674/692/719):
    fail_class = rule.get("fail_class") or FAIL_BUSINESS
Every result copies the rule's declared class. Nothing CHOOSES between the two
values based on what was observed, so a rule whose regex was malformed reports
the same class as one whose data violated it.

WHAT THIS MODULE DELIVERS
------------------------
Not a backfill. A MEASUREMENT. `link_report()` counts, per level:

    classifiable   — the class can be DERIVED from signals already stored
    unclassifiable — no stored signal separates the classes, so writing one
                     would be INVENTING it

The second number is the honest size of the gap, and it is precisely what
"more different failures" cannot answer today.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

CLASS_COL = "failure_class"
INVARIANT = "verdict=FAIL requires a class; verdict=PASS forbids one"

# ---------------------------------------------------------------------------
# THE EXTRACTOR — and the correction that produced it
# ---------------------------------------------------------------------------
# MY FIRST VERSION RETURNED 0 OF 2394 CLASSIFIABLE. THAT NUMBER WAS WRONG.
#
# It read `errors` as free text and set `cited=False` merely because the class
# column was absent, so every row landed on `provenance_defect` and none
# classified. I reported "no signal exists". The measurement below says the
# opposite: `errors` is a JSON LIST OF STRUCTURED MESSAGES, and 1484 of 2394
# NAME the registry or allowlist that rejected the row:
#
#     "Field: unknown on 'code_registry' (db_field_registry): bad_column"   97
#     "Capability: unknown or inactive '...' (not in capability_registry)"  97
#     "Table: blank not allowed — use 'n/a' if no table"                   181
#
# A broken extractor and a genuine gap BOTH return zero. That is why the zero
# was worthless without the positive control, and why the extractor is now the
# measured thing rather than an assumption.
#
# The registry names are VERIFIED to be real tables (all 7 exist in
# sqlite_master), so a class derived from naming one is grounded.
EXTRACT_VERSION = "v2_documented"

_RX_REGISTRY = re.compile(
    r"\b(capability_registry|api_registry|function_registry|module_registry|"
    r"channel_registry|db_field_registry|db_table_registry|allowlist)\b", re.I)
_RX_ABSENCE = re.compile(
    r"\b(empty|blank|missing|required|absent|no value)\b", re.I)
_RX_UNKNOWN = re.compile(r"\b(unknown|not registered|inactive|not in)\b", re.I)
# No row in this database currently matches this. Kept as an explicit, testable
# predicate rather than an omission, so `transient_execution` is DERIVED when the
# observable appears and is otherwise provably NOT derivable here.
_RX_RETRYABLE = re.compile(
    r"\b(timeout|timed out|connection|refused|unavailable|busy|locked|"
    r"deadlock|transient)\b", re.I)


def signals_from_row(row: dict[str, Any], table: str) -> dict[str, Any]:
    """Derive signals that are ACTUALLY OBSERVABLE. Never invent one.

    Every signal is backed by a measurement on a stored column, recorded here so
    a future reader can re-derive it:

      rule_key  <- a registry/allowlist is NAMED in the error text, and that name
                   is a real table. This is what makes `business_defect`
                   derivable: there is a rule to cite.
      slots_ok  <- the text NAMES an absence ("empty", "blank", "missing").
                   Absence IS the shape observation, which is why the first
                   version's `slots_ok=True` was an assertion CONTRADICTING what
                   the row said.
      meaning_ok<- the text names something unknown/inactive AND no registry is
                   cited, i.e. nothing was violated beyond the name not matching.
      cited     <- MEASURED: a trace_id and a non-empty input_payload exist, so
                   the finding can be traced. (2394/2394 here, so
                   `provenance_defect` is provably not derivable at this level —
                   reported, not hidden.)
      retryable <- MEASURED from the text. 0/2394 here, so
                   `transient_execution` is provably not derivable at this level.
    """
    import failure_axis as fa

    sig = dict(fa.BENIGN)
    text = str((row.get("errors") if table == "audit_trace"
                else row.get("error_reason")) or "")

    m_reg = _RX_REGISTRY.search(text)
    absence = bool(_RX_ABSENCE.search(text))
    unknown = bool(_RX_UNKNOWN.search(text))

    sig["parses"] = True                     # a stored, parseable message exists
    sig["slots_ok"] = not absence            # an absence observation is a shape fact
    sig["meaning_ok"] = not (unknown and not m_reg)
    # A rule is citeable when a real registry is named and the defect is NOT
    # merely "a value was absent".
    sig["rule_key"] = ("observed:%s" % m_reg.group(1).lower()
                       if (m_reg and not absence) else None)
    sig["retryable"] = bool(_RX_RETRYABLE.search(text))
    # `cited` is measured, not assumed: the row must be traceable to its payload.
    sig["cited"] = bool(str(row.get("trace_id") or "").strip()) and bool(
        str(row.get("input_payload") or "").strip() not in ("", "{}"))
    return sig


# Which classes this level's stored signals can actually SEPARATE. Derived from
# the extractor, and asserted in the proof, so adding a class that nothing can
# derive is a visible change rather than a silent decoration.
def derivable_classes(conn: sqlite3.Connection, table: str = "audit_trace",
                      verdict_col: str = "verdict",
                      fail_value: int = 0) -> dict:
    """Which classes does THIS LEVEL actually produce, measured over real rows.

    RETRACTED FIRST ATTEMPT: this used four hand-written probe rows, including a
    synthetic "Timeout" and a synthetic untraced row. Those probes reported all
    five classes reachable — which is exactly the invention this module exists to
    refuse. A probe is evidence; a fabricated probe is a fabricated finding.

    The honest measurement is over the stored rows: a class is REACHABLE when at
    least one real failing row derives to it, and NOT DERIVABLE at this level
    when no real row does. The undeliverable set is reported, never filled.
    """
    import failure_axis as fa

    allowed = fa.registered_classes(conn)
    seen: dict[str, int] = {}
    ambiguous = 0
    unclassifiable = 0
    for r in conn.execute("SELECT * FROM %s WHERE %s=%d"
                          % (table, verdict_col, int(fail_value))):
        res = fa.classify_failure(signals_from_row(dict(r), table),
                                  registered=allowed)
        if res["ok"]:
            c = res["failure_class"]
            seen[c] = seen.get(c, 0) + 1
        elif res["ambiguous"]:
            ambiguous += 1
        else:
            unclassifiable += 1
    return {
        "extract_version": EXTRACT_VERSION,
        "measured_over": "stored rows (no synthetic probe)",
        "reachable": sorted(seen),
        "counts": seen,
        "not_derivable_at_this_level": sorted(set(allowed) - set(seen)),
        "ambiguous": ambiguous,
        "unclassifiable": unclassifiable,
    }


# Levels that record a failure, with the column that decides pass/fail.
LEVELS: tuple[dict[str, Any], ...] = (
    {"level": "capability", "table": "audit_trace", "pk": "trace_id",
     "verdict_col": "verdict", "fail_value": 0, "pass_value": 1,
     "cite_template": "init_ontology_registry.sql:186"},
    {"level": "task", "table": "skill_mismatch_log", "pk": "mismatch_key",
     "verdict_col": None, "fail_value": None, "pass_value": None,
     "cite_template": "db_schema.py:1598"},
)


class FailureLinkError(ValueError):
    """Raised when a link would violate the class invariant."""


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def has_class_column(conn: sqlite3.Connection, table: str) -> bool:
    return CLASS_COL in _cols(conn, table)


def assert_class_column(conn: sqlite3.Connection, table: str,
                        *, allow_add: bool = True) -> dict[str, Any]:
    """Ensure the level CAN hold a class. Never touches `verdict`."""
    cols = _cols(conn, table)
    if CLASS_COL in cols:
        return {"table": table, "action": "present", "added": False}
    if not allow_add:
        raise FailureLinkError("%s has no %s column" % (table, CLASS_COL))
    # Nullable ON PURPOSE: a PASS has no class, and the invariant below is what
    # enforces that — NOT a NOT NULL constraint, which would forbid a PASS.
    conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (table, CLASS_COL))
    conn.commit()
    return {"table": table, "action": "added", "added": True, "nullable": True,
            "note": "verdict column untouched"}


def assert_invariant(conn: sqlite3.Connection, *, table: str = "audit_trace",
                     verdict_col: str = "verdict", fail_value: int = 0,
                     pass_value: int = 1) -> dict[str, Any]:
    """Check the class invariant in BOTH directions. Report, never repair."""
    if not has_class_column(conn, table):
        raise FailureLinkError("%s has no %s column" % (table, CLASS_COL))
    # The verdict value is an int internal to this module, so it is inlined
    # rather than bound — `?` with no parameter raised ProgrammingError, and a
    # placeholder that is never fed is worse than no placeholder.
    missing = conn.execute(
        "SELECT COUNT(*) FROM %s WHERE %s=%d AND (%s IS NULL OR TRIM(%s)='')"
        % (table, verdict_col, int(fail_value), CLASS_COL, CLASS_COL)).fetchone()[0]
    spurious = conn.execute(
        "SELECT COUNT(*) FROM %s WHERE %s=%d AND %s IS NOT NULL"
        % (table, verdict_col, int(pass_value), CLASS_COL)).fetchone()[0]
    return {"ok": missing == 0 and spurious == 0, "table": table,
            "invariant": INVARIANT,
            "fail_rows_without_class": missing,
            "pass_rows_with_class": spurious}


def link_failure_class(conn: sqlite3.Connection, *, table: str, pk_col: str,
                       pk_value: Any, signals: dict[str, Any],
                       allowed_classes: list[str] | None = None) -> dict[str, Any]:
    """DERIVE the class and write it. Refuses if the derivation refuses.

    The class is never passed in as a string. A caller supplies the SIGNALS it
    observed; `failure_axis.classify_failure` decides, and the citation comes
    from `failure_axis.CLASS_CITE` so every written class points at the
    definition it was chosen by.
    """
    import failure_axis as fa

    res = fa.classify_failure(signals, registered=allowed_classes)
    if not res["ok"]:
        return {"ok": False, "written": False, "why": res["why"],
                "matched": res["matched"], "pk": pk_value}
    cite = fa.CLASS_CITE.get(res["failure_class"])
    if not cite:
        raise FailureLinkError(
            "class %r has no registered definition to cite" % res["failure_class"])
    conn.execute("UPDATE %s SET %s=? WHERE %s=?" % (table, CLASS_COL, pk_col),
                 (res["failure_class"], pk_value))
    conn.commit()
    return {"ok": True, "written": True, "failure_class": res["failure_class"],
            "cite_ref": cite, "pk": pk_value, "why": res["why"]}


def link_report(conn: sqlite3.Connection, *, table: str = "audit_trace",
                verdict_col: str = "verdict", fail_value: int = 0,
                allowed_classes: list[str] | None = None) -> dict[str, Any]:
    """How many failures CAN be classified, and how many CANNOT. Never guesses."""
    import failure_axis as fa

    if not has_class_column(conn, table):
        raise FailureLinkError(
            "%s has no %s column; run assert_class_column first" % (table, CLASS_COL))
    allowed = allowed_classes or fa.registered_classes(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM %s WHERE %s=%d" % (table, verdict_col, int(fail_value)))]
    classifiable = ambiguous = unclassifiable = 0
    for r in rows:
        res = fa.classify_failure(signals_from_row(r, table), registered=allowed)
        if res["ok"]:
            classifiable += 1
        elif res["ambiguous"]:
            ambiguous += 1
        else:
            unclassifiable += 1
    return {
        "table": table,
        "allowed_classes": allowed,
        "failing_rows": len(rows),
        "classifiable_from_stored_signals": classifiable,
        "ambiguous": ambiguous,
        "unclassifiable": unclassifiable,
        "already_classified": conn.execute(
            "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL" % (table, CLASS_COL)
        ).fetchone()[0],
        "gap": ("stored signals do not separate the classes for %d of %d failing "
                "rows, so their class would have to be INVENTED. This module "
                "reports that rather than backfilling it."
                % (unclassifiable, len(rows))),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="add the nullable failure_class column (verdict untouched)")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    out: dict[str, Any] = {}
    try:
        if args.apply:
            out["schema"] = [assert_class_column(conn, lv["table"])
                             for lv in LEVELS]
        if args.report or args.apply:
            out["report"] = link_report(conn)
            out["invariant"] = assert_invariant(conn)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()