"""derive_5w1h.py — GENERATE a subject's 5W1H from the SSOT, or REFUSE.

THE USER'S DESIGN, WHICH WAS ALREADY IN THE REPO (2026-09-24)
------------------------------------------------------------
    "it is work to you for how evidence by logic generator to have multi dimensional
     SSOT, we trust evidence only, not hardcode maker"

MEASURED: the multi-dimensional SSOT ALREADY EXISTS and my own work bypassed it:

  * `skill_5w1h.DIMENSIONS` — the SIX dimensions, each with a QUESTION and a
    CHECKABLE rule. Example: `how` = "a command that can be RUN or a check that can
    FAIL, not 'review it'".
  * `dimension_binding_registry` — **96 rows**: the SAME 6 dimensions bound PER
    `subject_kind`, each with `binding_text`, `example` and a `cite_ref`.

So the dimensions are declared ONCE and each kind binds them. This module GENERATES
a subject's 5W1H from that, and it deals with my own failure: I typed
`CAP.TASK_CENTER.ROLE_DERIVE`'s fields as `role/rule/evidence/cite/refused/
stop_reason` — satisfying a "6 mandatory fields" RULE while containing ZERO of the
six dimensions.

THE SIX DIMENSIONS ARE NEVER RE-TYPED HERE. They are READ from `skill_5w1h`, so a
7th dimension, or a sharper question, changes this module by itself.
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


def dimensions() -> dict[str, dict[str, Any]]:
    """The 6 dimensions, READ from `skill_5w1h.DIMENSIONS` (never re-typed).

    Returns `{name: {question, rule, mandatory}}` plus the order, so a caller can
    render them in the declared sequence.
    """
    import skill_5w1h as s5
    out: dict[str, dict[str, Any]] = {}
    for name, question, rule, mandatory in s5.DIMENSIONS:
        out[str(name)] = {"question": str(question), "rule": str(rule),
                          "mandatory": bool(mandatory)}
    return out


def dimension_names() -> tuple[str, ...]:
    """The dimension NAMES in declared order — from the SSOT, not a local list."""
    import skill_5w1h as s5
    return tuple(str(d[0]) for d in s5.DIMENSIONS)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                        (name,)).fetchone() is not None


def bindings(conn: sqlite3.Connection, subject_kind: str) -> dict[str, Any]:
    """The DECLARED bindings for a subject kind, READ from the SSOT.

    A `dimension_key` with no binding is REPORTED as missing. It is never filled
    with a default, because a default would be a value this module typed.
    """
    if not _table_exists(conn, "dimension_binding_registry"):
        return {"ok": False, "reason": "NO_BINDING_REGISTER"}
    rows = [dict(r) for r in conn.execute(
        "SELECT dimension_key, binding_text, example, cite_ref, is_active "
        "FROM dimension_binding_registry WHERE subject_kind = ? "
        "ORDER BY sort_order", (str(subject_kind),))]
    active = [r for r in rows if int(r.get("is_active") or 0) == 1]
    declared = {str(r["dimension_key"]) for r in active}
    missing = [d for d in dimension_names() if d not in declared]
    return {"ok": True, "subject_kind": str(subject_kind),
            "bindings": active, "count": len(active),
            "declared": sorted(declared),
            "missing": missing,
            "unknown_dimensions": sorted(declared - set(dimension_names())),
            "would_be_red_if": "a dimension has no binding and is defaulted instead"}


def fields(conn: sqlite3.Connection, subject_kind: str) -> dict[str, Any]:
    """GENERATE the 5W1H for a subject, REFUSING a dimension with no binding.

    THE OUTPUT IS DERIVED: each field carries the dimension's QUESTION (from the
    SSOT), the binding's TEXT (from the register) and its CITE. Nothing here is a
    value this module invented.
    """
    dims = dimensions()
    b = bindings(conn, subject_kind)
    if not b.get("ok"):
        return b
    if b["missing"]:
        # REFUSE rather than invent. A generated 5W1H with a made-up `how` is worse
        # than no 5W1H: it looks verified and is not.
        return {"ok": False, "reason": "MISSING_BINDING",
                "subject_kind": str(subject_kind), "missing": b["missing"],
                "why": ("a dimension with no binding must be DECLARED, not "
                        "defaulted; a generated value would be a typed value"),
                "declared": b["declared"]}
    by_dim = {str(r["dimension_key"]): r for r in b["bindings"]}
    out: list[dict[str, Any]] = []
    for name in dimension_names():
        d = dims[name]
        row = by_dim[name]
        out.append({
            "field_name": name,
            "question": d["question"],
            "hard_rule": d["rule"],
            "mandatory": d["mandatory"],
            "binding_text": str(row["binding_text"]),
            "example": str(row["example"]) if row.get("example") else None,
            "cite_ref": str(row["cite_ref"]),
        })
    return {"ok": True, "subject_kind": str(subject_kind),
            "fields": out, "count": len(out),
            "generated_from": ["skill_5w1h.DIMENSIONS",
                               "dimension_binding_registry"],
            "would_be_red_if": "a field's text does not trace to a binding row"}


def declare_binding(conn: sqlite3.Connection, subject_kind: str, dimension_key: str,
                    *, binding_text: str, example: str, cite_ref: str,
                    commit: bool = True) -> dict[str, Any]:
    """THE ONE write path for a new binding. Refuses anything uncitable.

    REFUSES:
      * `UNKNOWN_DIMENSION` — the dimension is not in the SSOT's vocabulary.
      * `UNCITED_BINDING`   — a binding with no citation is an uncheckable claim.
      * `EMPTY_BINDING`     — a blank binding means "declared" without saying what.
    """
    if dimension_key not in dimension_names():
        return {"ok": False, "reason": "UNKNOWN_DIMENSION",
                "dimension_key": str(dimension_key),
                "declared": list(dimension_names())}
    if not str(cite_ref or "").strip():
        return {"ok": False, "reason": "UNCITED_BINDING"}
    if not str(binding_text or "").strip():
        return {"ok": False, "reason": "EMPTY_BINDING"}
    if not _table_exists(conn, "dimension_binding_registry"):
        return {"ok": False, "reason": "NO_BINDING_REGISTER"}
    row = conn.execute(
        "SELECT binding_id FROM dimension_binding_registry WHERE subject_kind=? "
        "AND dimension_key=? AND is_active=1",
        (str(subject_kind), str(dimension_key))).fetchone()
    if row:
        conn.execute(
            "UPDATE dimension_binding_registry SET binding_text=?, example=?, "
            "cite_ref=?, updated_at=datetime('now') WHERE binding_id=?",
            (str(binding_text), str(example), str(cite_ref), row["binding_id"]))
        if commit:
            conn.commit()
        return {"ok": True, "changed": True, "binding_id": int(row["binding_id"]),
                "subject_kind": str(subject_kind), "dimension_key": str(dimension_key)}
    cur = conn.execute(
        "INSERT INTO dimension_binding_registry (subject_kind, dimension_key, "
        "binding_text, example, cite_ref, sort_order, is_active) "
        "VALUES (?,?,?,?,?,?,1)",
        (str(subject_kind), str(dimension_key), str(binding_text), str(example),
         str(cite_ref), dimension_names().index(str(dimension_key)) + 1))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "binding_id": int(cur.lastrowid),
            "subject_kind": str(subject_kind), "dimension_key": str(dimension_key)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dimensions", action="store_true")
    ap.add_argument("--bindings", metavar="SUBJECT_KIND", default=None)
    ap.add_argument("--fields", metavar="SUBJECT_KIND", default=None)
    ap.add_argument("--subject-kinds", action="store_true",
                    help="which subject kinds the SSOT already binds")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.subject_kinds:
            out = {"ok": True, "subject_kinds": [
                {"subject_kind": str(r["subject_kind"]), "bindings": int(r["n"]),
                 "declared": int(r["declared"] or 0)}
                for r in conn.execute(
                    "SELECT subject_kind, COUNT(*) n, SUM(is_active=1) declared "
                    "FROM dimension_binding_registry GROUP BY subject_kind "
                    "ORDER BY subject_kind")]}
        elif a.fields:
            out = fields(conn, a.fields)
        elif a.bindings:
            out = bindings(conn, a.bindings)
        else:
            out = {"ok": True, "dimensions": dimensions(),
                   "order": list(dimension_names()),
                   "source": "skill_5w1h.DIMENSIONS"}
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.subject_kinds:
        print("subject kinds in dimension_binding_registry:")
        for s in out["subject_kinds"]:
            flag = "ok " if s["declared"] == len(dimension_names()) else "GAP"
            print("   %-3s %-22s bindings=%-3d declared=%d"
                  % (flag, s["subject_kind"], s["bindings"], s["declared"]))
    elif a.fields:
        if not out.get("ok"):
            print("REFUSED: %s" % out.get("reason"))
            if out.get("missing"):
                print("   missing bindings: %s" % out["missing"])
            if out.get("why"):
                print("   why: %s" % out["why"])
            return 0
        print("GENERATED 5W1H for subject_kind=%r (%d fields)"
              % (out["subject_kind"], out["count"]))
        print("from: %s" % ", ".join(out["generated_from"]))
        for f in out["fields"]:
            print("   %-7s %s" % (f["field_name"], f["question"]))
            print("           rule   : %s" % f["hard_rule"][:90])
            print("           binding: %s  [%s]"
                  % (f["binding_text"][:70], f["cite_ref"]))
    elif a.bindings:
        print("subject_kind=%r bindings=%d" % (out["subject_kind"], out["count"]))
        for b in out["bindings"]:
            print("   %-7s %s  [%s]" % (b["dimension_key"],
                                        str(b["binding_text"])[:64], b["cite_ref"]))
        if out["missing"]:
            print("   MISSING: %s" % out["missing"])
    else:
        print("the 6 dimensions, READ from skill_5w1h.DIMENSIONS:")
        for name in out["order"]:
            d = out["dimensions"][name]
            print("   %-7s %s" % (name, d["question"]))
            print("           rule: %s" % d["rule"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
