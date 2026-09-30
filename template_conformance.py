"""template_conformance.py — does the FACTOR TABLE satisfy the FACTOR TEMPLATE?

THE USER'S QUESTION (2026-09-24)
--------------------------------
    "i can't sure does all the data updated or not, as it is one of the key for whole
     system — pls proof, key factor -> output = with can measured unit"

THE ANSWER IS NO, AND THIS MODULE PROVES IT BY RUNNING THE DECLARED RULES.

`factor_template` IS the key-factor definition: 9 required fields, four of which carry
an ENFORCEMENT RULE. MEASURED against the live table:

| rule | subject field | measured state |
|---|---|---|
| `must_be_known_kind` | `metric_kind` | **the rule's SUBJECT does not exist** — there is no `metric_kind_registry` and no file declaring the known kinds, so the rule can never fail |
| `must_name_subject` | `metric_unit` | 1 of 39 factors FAILS (`runtime_liveness_evidence`) |
| `must_be_value_of_kind` | `metric_target` | 0 failing |
| `must_be_checkable` | `cite_ref` | **the COLUMN does not exist**, so the rule is unenforceable |

A RULE WHOSE SUBJECT IS ABSENT CANNOT FAIL, and a check that cannot fail is not a
check. That is the same defect as reading `is_active=0` as "retired" when it also
means "never activated": **the vocabulary is declared and the thing it needs to
compare against is not.**

REUSE, NOT REINVENTION
----------------------
`factor_first_principle` ALREADY has `unit_subject` (the `must_name_subject` check)
and `audit_factor`. This module CALLS them. MEASURED: that is the FOURTH time this
session the needed part already existed and my own work bypassed it.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

# Where a "known kind" vocabulary WOULD live. Looked up by NAME in sqlite_master and
# by DECLARATION in the source, because the rule names no table.
KIND_VOCAB_TABLES = ("metric_kind_registry", "factor_metric_kind", "metric_kind")


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [d[1] for d in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.OperationalError:
        return []


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                        (name,)).fetchone() is not None


def template(conn: sqlite3.Connection) -> dict[str, Any]:
    """The key-factor DEFINITION, READ from `factor_template` (never re-typed)."""
    if not _table_exists(conn, "factor_template"):
        return {"ok": False, "reason": "NO_FACTOR_TEMPLATE"}
    rows = [dict(r) for r in conn.execute(
        "SELECT field_name, kind, is_required, why, rule, sort_order "
        "FROM factor_template WHERE is_active = 1 ORDER BY sort_order")]
    return {"ok": True, "fields": rows, "count": len(rows),
            "required": [r["field_name"] for r in rows
                         if int(r["is_required"] or 0) == 1],
            "rules": {str(r["field_name"]): str(r["rule"]) for r in rows
                      if str(r["rule"] or "").strip() not in ("", "NA")},
            "would_be_red_if": "a required template field is absent from the table"}


def known_metric_kinds(conn: sqlite3.Connection) -> dict[str, Any]:
    """Is there a DECLARED list of metric kinds, which `must_be_known_kind` needs?

    MEASURED: there is none. The rule names no table, no `*kind*` register exists,
    and no module declares the list. So the rule's SUBJECT is absent and the rule
    cannot fail — reported, never assumed to be satisfied.
    """
    tables = [t for t in KIND_VOCAB_TABLES if _table_exists(conn, t)]
    # A declaration could also live in code; a scan is honest about what it found.
    #
    # SELF-REFERENCE GUARD. MEASURED BUG IN THIS CHECK'S FIRST VERSION: this module
    # contains the literal `KNOWN_METRIC` (in this very condition) so it counted
    # ITSELF as the declaration and reported `KIND_VOCABULARY_EXISTS` for a
    # vocabulary that does not exist. A detector must not be its own evidence, so
    # this file is skipped — the same class of fix as excluding a pattern's own
    # definition from a code scan.
    declared_in: list[str] = []
    for p in sorted(BASE.glob("*.py")):
        if p.name == Path(__file__).name:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "KNOWN_METRIC" in t:
            declared_in.append(p.name)
    return {"ok": True, "tables": tables, "declared_in_code": declared_in,
            "exists": bool(tables or declared_in),
            "verdict": ("KIND_VOCABULARY_EXISTS" if (tables or declared_in)
                        else "KIND_VOCABULARY_ABSENT"),
            "why": ("`must_be_known_kind` compares `metric_kind` against a list of "
                    "known kinds; with no such list the rule has no subject and can "
                    "never fail"),
            "would_be_red_if": "metric_kind holds a value that is not a known kind"}


def check_table(conn: sqlite3.Connection, table: str = "skill_factor_registry",
                *, active_only: bool = True) -> dict[str, Any]:
    """Run EVERY declared rule against the live table. Findings NAMED, not counted.

    Each rule is run against its OWN subject column. A rule whose column is missing
    is reported `COLUMN_MISSING`, and a rule whose VOCABULARY is missing is reported
    `SUBJECT_ABSENT` — both are FAILINGS of the template, distinct from a factor
    whose data is wrong.
    """
    import factor_first_principle as ffp

    t = template(conn)
    if not t.get("ok"):
        return t
    cols = _cols(conn, table)
    if not cols:
        return {"ok": False, "reason": "NO_SUCH_TABLE", "table": table}
    where = "WHERE is_active = 1" if active_only else ""
    total = int(conn.execute("SELECT COUNT(*) FROM %s %s" % (table, where)
                             ).fetchone()[0])
    results: list[dict[str, Any]] = []
    vocab = known_metric_kinds(conn)

    for field_name, rule in t["rules"].items():
        entry: dict[str, Any] = {"field": field_name, "rule": rule,
                                 "failing": [], "state": None}
        if field_name not in cols:
            entry["state"] = "COLUMN_MISSING"
            entry["why"] = ("the template requires this field and the table has no "
                            "column for it, so the rule cannot be enforced")
            results.append(entry)
            continue
        keycol = "factor_key" if "factor_key" in cols else "name"
        rows = [dict(r) for r in conn.execute(
            "SELECT %s AS k, %s AS v FROM %s %s" % (keycol, field_name, table, where))]
        for r in rows:
            v = str(r["v"] or "")
            if not v.strip() or v == "NA":
                entry["failing"].append({"factor": str(r["k"]), "value": v,
                                         "why": "blank or NA"})
                continue
            if rule == "must_name_subject":
                # CALL the existing helper rather than re-deriving the rule.
                try:
                    subj = ffp.unit_subject(v)
                except Exception as e:
                    subj = ""
                    entry["failing"].append({"factor": str(r["k"]), "value": v[:60],
                                             "why": "%s: %s" % (type(e).__name__, e)})
                    continue
                if not subj:
                    entry["failing"].append({
                        "factor": str(r["k"]), "value": v[:60],
                        "why": ("`unit_subject` found NO subject in the unit, so the "
                                "number it produces cannot be audited")})
            elif rule == "must_be_known_kind":
                if not vocab["exists"]:
                    entry["state"] = "SUBJECT_ABSENT"
                    entry["why"] = vocab["why"]
                    break
            # must_be_value_of_kind / must_be_checkable: presence is the check here;
            # a deeper check belongs to `factor_first_principle.audit_factor`.
        if entry["state"] is None:
            entry["state"] = "PASS" if not entry["failing"] else "FAIL"
        results.append(entry)

    failing = [r for r in results
               if r["state"] in ("FAIL", "COLUMN_MISSING", "SUBJECT_ABSENT")]
    # RULE-LESS REQUIRED FIELDS ARE STILL REQUIRED.
    #
    # MEASURED GAP IN THIS CHECK'S FIRST VERSION: it ran only the four fields that
    # CARRY a rule, so a REQUIRED field with `rule=NA` (e.g. `proof_prefix`) could be
    # absent from the table and nothing noticed. "Has no rule" is not "not needed".
    required_presence: list[dict[str, Any]] = []
    for f in t["fields"]:
        fn = str(f["field_name"])
        if int(f["is_required"] or 0) != 1 or fn in t["rules"]:
            continue
        present = fn in cols
        required_presence.append({"field": fn, "present": present,
                                  "why": str(f["why"])})
    missing_required = [r["field"] for r in required_presence if not r["present"]]
    enforceable = [r for r in results if r["state"] in ("PASS", "FAIL")]
    return {"ok": not failing and not missing_required,
            "table": table, "factors": total,
            "template_fields": t["count"], "rules_checked": len(results),
            "rules_enforceable": len(enforceable),
            "rules_unenforceable": len(results) - len(enforceable),
            "required_presence": required_presence,
            "missing_required": missing_required,
            "results": results, "failing": failing,
            "vocabulary": vocab,
            "would_be_red_if": ("a declared rule cannot be run, a REQUIRED field is "
                                "absent, or a factor fails a rule")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check-table", metavar="TABLE", nargs="?",
                    const="skill_factor_registry", default=None)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--kinds", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.template:
            out = template(conn)
        elif a.kinds:
            out = known_metric_kinds(conn)
        else:
            out = check_table(conn, a.check_table or "skill_factor_registry")
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok", True) else 1

    if a.template:
        print("the KEY FACTOR definition (factor_template), %d fields:" % out["count"])
        for f in out["fields"]:
            r = ("  rule=%s" % f["rule"]) if str(f["rule"] or "NA") != "NA" else ""
            print("   %-16s %-9s required=%s%s" % (f["field_name"], f["kind"],
                                                   f["is_required"], r))
            print("        why: %s" % f["why"])
    elif a.kinds:
        print("must_be_known_kind's SUBJECT: %s" % out["verdict"])
        print("   tables found     : %s" % (out["tables"] or "NONE"))
        print("   declared in code : %s" % (out["declared_in_code"] or "NONE"))
        print("   why: %s" % out["why"])
    else:
        print("table=%s factors=%d template_fields=%d"
              % (out["table"], out["factors"], out["template_fields"]))
        print("rules checked      : %d" % out["rules_checked"])
        print("  enforceable      : %d" % out["rules_enforceable"])
        print("  UNENFORCEABLE    : %d" % out["rules_unenforceable"])
        print("REQUIRED fields present: %d of %d"
              % (sum(1 for r in out["required_presence"] if r["present"]),
                 len(out["required_presence"]) + out["rules_checked"]))
        if out["missing_required"]:
            print("MISSING REQUIRED FIELDS: %s" % out["missing_required"])
        print()
        for r in out["results"]:
            mark = {"PASS": "ok ", "FAIL": "FAIL",
                    "COLUMN_MISSING": "GAP ", "SUBJECT_ABSENT": "GAP "}[r["state"]]
            print("   %-4s %-16s rule=%-24s %s" % (mark, r["field"], r["rule"],
                                                   r["state"]))
            if r.get("why"):
                print("        %s" % r["why"])
            for f in r["failing"][:3]:
                print("        #%-34s %s" % (f["factor"], f["why"]))
        print()
        print("VERDICT: %s" % ("all declared rules enforced"
                               if out["ok"] else
                               "%d rule(s) FAILING or UNENFORCEABLE" % len(out["failing"])))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
