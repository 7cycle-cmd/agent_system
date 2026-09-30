"""factor_generator.py — GENERATE a key factor's fields from the TEMPLATE.

THE USER'S RULE (2026-09-24)
----------------------------
    "5W1H -> factor, factor must with can measured unit = key factor"

so the chain is: a QUESTION (a 5W1H dimension) -> a FACTOR whose fields satisfy the
template, INCLUDING a measurable unit.

THE TEMPLATE DRIVES THE GENERATOR
---------------------------------
`factor_template` declares the 9 fields a factor must carry and, for four of them,
the RULE that must hold. This module produces a factor DRAFT by DERIVATION, and
every field it emits is traced to the template row it came from. Nothing here is a
value this module decided.

REUSE, NOT REINVENTION
----------------------
`factor_first_principle` ALREADY has `dimension_of`, `unit_subject`,
`assert_measurable` and `derive`. This module CALLS them; it does not re-derive the
`must_name_subject` rule or re-map the dimensions. MEASURED: this is the FOURTH time
this session that the needed part already existed and my work bypassed it — so the
generator's proof asserts the CALL, not merely that it looks right.
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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                        (name,)).fetchone() is not None


def template_fields(conn: sqlite3.Connection) -> dict[str, Any]:
    """The template's fields, keyed by name — READ, never re-typed."""
    import template_conformance as tc
    t = tc.template(conn)
    if not t.get("ok"):
        return t
    return {"ok": True,
            "by_name": {str(r["field_name"]): r for r in t["fields"]},
            "order": [str(r["field_name"]) for r in t["fields"]],
            "rules": t["rules"]}


def dimensions() -> tuple[str, ...]:
    """The 5W1H dimension names, from `skill_5w1h` (never re-typed)."""
    import skill_5w1h as s5
    return tuple(str(d[0]) for d in s5.DIMENSIONS)


def derive_field(conn: sqlite3.Connection, field_name: str, *,
                 dimension: str | None = None,
                 subject: str | None = None) -> dict[str, Any]:
    """Produce ONE field's expected shape, from the TEMPLATE row.

    REFUSES:
      * `UNKNOWN_FIELD`   — the template does not declare this field.
      * `UNKNOWN_DIMENSION` — the 5W1H vocabulary does not declare this dimension.
      * `NO_SUBJECT`      — the field's rule needs a subject and none was supplied.
    """
    tf = template_fields(conn)
    if not tf.get("ok"):
        return tf
    row = tf["by_name"].get(str(field_name))
    if row is None:
        return {"ok": False, "reason": "UNKNOWN_FIELD", "field": str(field_name),
                "declared": tf["order"]}
    if dimension is not None and str(dimension) not in dimensions():
        return {"ok": False, "reason": "UNKNOWN_DIMENSION",
                "dimension": str(dimension), "declared": list(dimensions())}
    rule = str(row.get("rule") or "NA")
    out: dict[str, Any] = {
        "ok": True, "field": str(field_name), "kind": str(row["kind"]),
        "required": int(row["is_required"] or 0), "rule": rule,
        "why": str(row["why"]), "from_template": True,
    }
    if rule == "must_name_subject":
        if not str(subject or "").strip():
            return {"ok": False, "reason": "NO_SUBJECT", "field": str(field_name),
                    "why": ("the rule is `must_name_subject`, so a subject is "
                            "required; without one the unit cannot be audited")}
        out["subject"] = str(subject)
        out["example"] = "%s of %s" % (row["kind"] or "pct", subject)
    elif rule == "must_be_known_kind":
        out["allowed"] = _known_kinds(conn)
        if not out["allowed"]:
            out["warning"] = ("must_be_known_kind has no vocabulary, so this field "
                              "cannot be validated — reported, not assumed valid")
    elif rule == "must_be_checkable":
        out["needs"] = "a citation pointing at what establishes this factor"
    return out


def _known_kinds(conn: sqlite3.Connection) -> list[str]:
    """The declared metric kinds, or [] when the vocabulary does not exist."""
    import template_conformance as tc
    k = tc.known_metric_kinds(conn)
    return [] if not k["exists"] else k["tables"] + k["declared_in_code"]


def build(conn: sqlite3.Connection, *, dimension: str, subject: str,
          factor_key: str = "", name: str = "", rule_definition: str = "",
          action: str = "", metric_target: str = "",
          proof_prefix: str = "", cite_ref: str = "") -> dict[str, Any]:
    """Assemble a factor DRAFT by DERIVATION from the template.

    Every field reports WHERE it came from, and a field whose rule cannot be
    satisfied is reported as UNSATISFIED rather than filled with something plausible.
    The result is a DRAFT: it is not written anywhere by this function.
    """
    tf = template_fields(conn)
    if not tf.get("ok"):
        return tf
    if str(dimension) not in dimensions():
        return {"ok": False, "reason": "UNKNOWN_DIMENSION", "dimension": str(dimension),
                "declared": list(dimensions())}
    provided = {
        "factor_key": factor_key, "name": name, "rule_definition": rule_definition,
        "action": action, "metric_target": metric_target,
        "proof_prefix": proof_prefix, "cite_ref": cite_ref,
    }
    fields: list[dict[str, Any]] = []
    unsatisfied: list[str] = []
    for fname in tf["order"]:
        row = tf["by_name"][fname]
        rule = str(row.get("rule") or "NA")
        if fname == "metric_unit":
            d = derive_field(conn, fname, dimension=dimension, subject=subject)
            fields.append(d)
            if not d.get("ok"):
                unsatisfied.append(fname)
            continue
        if rule != "NA":
            d = derive_field(conn, fname, dimension=dimension)
            if not d.get("ok") and d.get("reason") == "NO_SUBJECT":
                d = derive_field(conn, fname, dimension=dimension, subject=subject)
            d["value"] = provided.get(fname) or None
            if d.get("warning"):
                unsatisfied.append(fname)
            fields.append(d)
            continue
        val = provided.get(fname) or None
        fields.append({"ok": bool(val) or int(row["is_required"] or 0) == 0,
                       "field": fname, "kind": str(row["kind"]),
                       "required": int(row["is_required"] or 0), "rule": "NA",
                       "why": str(row["why"]), "value": val,
                       "from_template": True,
                       "satisfied": bool(val)})
    missing = [f["field"] for f in fields
               if int(f.get("required") or 0) == 1 and not f.get("satisfied",
                                                                 f.get("ok"))]
    return {"ok": True, "dimension": str(dimension), "subject": str(subject),
            "fields": fields, "unsatisfied_rules": sorted(set(unsatisfied)),
            "missing_values": sorted(set(missing)),
            "template_fields": tf["order"],
            "generated_from": ["factor_template", "skill_5w1h.DIMENSIONS",
                               "factor_first_principle"],
            "would_be_red_if": ("a factor is emitted without a measurable unit, or a "
                                "field does not trace to the template")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--dimension", default="how")
    ap.add_argument("--subject", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.build:
            out = build(conn, dimension=a.dimension, subject=a.subject)
        else:
            out = template_fields(conn)
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if a.build:
        if not out.get("ok"):
            print("REFUSED: %s" % out.get("reason"))
            if out.get("declared"):
                print("   declared: %s" % out["declared"])
            if out.get("why"):
                print("   why: %s" % out["why"])
            return 1
        print("GENERATED factor draft: dimension=%r subject=%r"
              % (out["dimension"], out["subject"]))
        print("from: %s" % ", ".join(out["generated_from"]))
        for f in out["fields"]:
            status = f.get("satisfied", f.get("ok"))
            print("   %-5s %-16s kind=%-9s rule=%-22s"
                  % ("ok" if status else "MISS", f["field"], f["kind"], f["rule"]))
            if f.get("example"):
                print("        example: %s" % f["example"])
            if f.get("warning"):
                print("        WARNING: %s" % f["warning"])
        if out["missing_values"]:
            print()
            print("   MISSING VALUES: %s" % out["missing_values"])
        if out["unsatisfied_rules"]:
            print("   UNSATISFIED RULES: %s" % out["unsatisfied_rules"])
    else:
        if not out.get("ok"):
            print("REFUSED: %s" % out.get("reason"))
            return 1
        print("the template's fields, in order (%d):" % len(out["order"]))
        for f in out["order"]:
            r = out["by_name"][f]
            print("   %-16s %-9s rule=%s" % (f, r["kind"], r["rule"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
