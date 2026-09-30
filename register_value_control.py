"""register_value_control.py — ONE value model for the whole `*_register` family.

THE USER'S PROPOSAL (2026-09-24)
--------------------------------
    "does value define as terminology can be apply for *_register, so management can
     be more easier, special for the version update
     old = is_active = 0 , new = is_active=1
     by the value control, easy to have the too?"

THE RULING, AFTER MEASURING (26 `*register` tables)
---------------------------------------------------
The man is RIGHT that ONE model should govern the family, and `is_active` IS that
value — it already means old/new in **22 of 26** registers. What is missing is not a
new idea but a **DECLARATION + an AUDIT**, because "it applies to all registers" is
currently an assumption nothing checks, and the two things the model is expected to
deliver do NOT hold today:

  1. **`version` cannot drive a version update.** MEASURED: EVERY row in EVERY
     register has `version = 1`. A control that reads a constant controls nothing.
     The honest statement is `VERSION_IS_DECLARED_NOT_POPULATED`, and the version
     update must go through `is_active` — retire the old row, activate the new.
     That IS the user's rule; it is the `version` column that is not ready.
  2. **The name-link does not exist.** MEASURED: **6 of 283** register name-keys
     appear in `terminology_registry` (2.1%). So "manage one value across registers"
     cannot be built on `terminology_registry` alone; it must go through the ONE
     DOOR and the gap must be REPORTED, never assumed.

WHY THIS IS NOT A NEW TABLE
---------------------------
`is_active` IS the lifecycle and it already exists in 22 registers. A second table
saying "is this current" would be a second place for ONE fact to disagree with
itself — the defect family this session has removed four times. So this module is a
MODEL + AUDIT + ONE WRITE PATH, using the columns the registers already have.

THE EXCEPTIONS ARE NAMED, NOT FORCED
------------------------------------
Four registers have no lifecycle and get a NAMED reason instead of a synthetic
column: `code_registry`, `register_approve`, `register_fill_control`,
`register_fill_run`. Forcing `is_active` onto them would be inventing state.
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

# ---------------------------------------------------------------------------
# THE MODEL, AS DATA. One place to read what "old" and "new" mean, so a reader
# does not have to infer it from code (the user's "by the value control").
# ---------------------------------------------------------------------------
VALUE_MODEL: dict[str, Any] = {
    "value_column": "is_active",
    "new": 1,
    "old": 0,
    "version_column": "version",
    "meaning": {
        1: "the CURRENT row for this key",
        0: "SUPERSEDED or retired — kept as history, never deleted",
    },
    "rule": ("a version update RETIRES the old row (is_active=0) and ACTIVATES the "
             "new one (is_active=1); it never deletes, so the old value stays "
             "readable and the change is traceable"),
    "not_a_second_table": ("is_active IS the lifecycle; a second 'is this current' "
                           "table would duplicate one fact"),
}

# Registers with NO lifecycle column, each with the reason it is an EXCEPTION
# rather than a defect. Forcing `is_active` onto these would invent state.
NOT_APPLICABLE: dict[str, str] = {
    "code_registry": ("a CODE INVENTORY: a source file is not 'old' while it exists; "
                      "its currency is decided by the tree and git, not by a flag"),
    "register_approve": ("an APPROVAL LOG: every row is an event that happened; an "
                         "event is never superseded, so a lifecycle flag is meaningless"),
    "register_fill_control": ("a FILL CONTROL counter: it holds 'how far the fill got', "
                              "which is state, not a versioned identity"),
    "register_fill_run": ("a RUN LOG: one row per execution; runs are history, not "
                          "competing current values"),
}

# The key column to use per register, so a caller names a KEY and not a table shape.
# Read from PRAGMA when a register is not listed here.
KEY_HINTS: dict[str, str] = {
    "terminology_registry": "term_key",
    "skill_registry": "skill_key",
    "skill_factor_registry": "factor_key",
    "prompt_registry": "prompt_key",
    "wording_registry": "wording_key",
    "worker_registry": "worker_key",
    "workflow_registry": "workflow_key",
    "mode_registry": "mode_key",
    "mode_right_registry": "mode_key",
    "route_registry": "route_key",
    "purpose_route_registry": "route_key",
    "subject_kind_registry": "kind_key",
    "identity_registry": "identity_key",
    "study_registry": "study_key",
    "component_registry": "skill_key",
    "dimension_binding_registry": "dimension_key",
    # `code_location_registry.cite_ref` and `version_registry.version_registry_id`
    # are NOT name keys:
    #   * a `cite_ref` is a CITATION ('db_schema.py:1'), not the thing's name;
    #   * `version_registry_id` is the PRIMARY KEY.
    # MEASURED BUG IN THIS MODULE'S FIRST VERSION: treating them as names put 1410
    # citations/ids into the denominator, so name-coverage read 7.0% instead of the
    # real ~2%. Mapping a key to None EXCLUDES the register from name coverage,
    # because it has no name to be covered.
    "code_location_registry": None,
    "version_registry": None,
}

# Registers whose only candidate key is NOT a name — used by `name_coverage` to
# report them as `NOT_APPLICABLE` rather than silently inflating a percentage.
NO_NAME_KEY: dict[str, str] = {
    "code_location_registry": "its key is a citation, not a name",
    "version_registry": "its key is a primary key, not a name",
    "derived_column_registry": "its key is a citation, not a name",
}


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [d[1] for d in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.OperationalError:
        return []


def registers(conn: sqlite3.Connection) -> list[str]:
    """Every `*register` object, as a NAME (a table or a view — no kind asserted)."""
    return [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name LIKE '%register%' ORDER BY name")]


def key_column_for(conn: sqlite3.Connection, register: str) -> str | None:
    """The NAME column of a register, derived. `None` when it has no name.

    This is the derivation `set_lifecycle` uses when a caller supplies only a
    register and a key, so one call shape works across the family.
    """
    if register in KEY_HINTS:
        return KEY_HINTS[register]
    cols = _cols(conn, register)
    for cand in ("_key", "_ref"):
        for c in cols:
            if c.endswith(cand) and c != "cite_ref":
                return c
    return "name" if "name" in cols else None


def key_column(conn: sqlite3.Connection, register: str) -> str | None:
    """DEPRECATED ALIAS of `key_column_for`.

    Kept because the audit and the name-coverage report were written against this
    name, and a rename that silently changes a public symbol is the kind of drift
    this repo removes. New code should call `key_column_for`.
    """
    return key_column_for(conn, register)


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which registers CONFORM to the value model, counted and NAMED.

    Three classes, so a reader can act on a number instead of a claim:
      * `conformant`  — has `is_active`; the old/new rule applies as-is.
      * `exception`   — no `is_active`, with a NAMED `NOT_APPLICABLE` reason.
      * `unexplained` — no `is_active`, no reason: a REAL GAP to be decided.
    """
    rows: list[dict[str, Any]] = []
    for t in registers(conn):
        cols = _cols(conn, t)
        has = "is_active" in cols
        try:
            n = int(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
        except sqlite3.OperationalError:
            n = -1
        active = inactive = None
        if has and n > 0:
            a = conn.execute("SELECT SUM(is_active=1), SUM(is_active=0) "
                             "FROM %s" % t).fetchone()
            active = int(a[0] or 0)
            inactive = int(a[1] or 0)
        if has:
            cls, why = "conformant", None
        elif t in NOT_APPLICABLE:
            cls, why = "exception", NOT_APPLICABLE[t]
        else:
            cls, why = "unexplained", "no is_active and no declared reason"
        rows.append({
            "register": t, "rows": n, "class": cls,
            "has_is_active": has, "active": active, "inactive": inactive,
            "key_column": key_column(conn, t), "reason": why,
            "has_version": "version" in cols,
        })
    conf = [r for r in rows if r["class"] == "conformant"]
    exc = [r for r in rows if r["class"] == "exception"]
    unexp = [r for r in rows if r["class"] == "unexplained"]
    return {"ok": not unexp, "registers": rows,
            "counts": {"total": len(rows), "conformant": len(conf),
                       "exception": len(exc), "unexplained": len(unexp)},
            "unexplained": [r["register"] for r in unexp],
            "model": VALUE_MODEL,
            "would_be_red_if": "a register has no lifecycle and no named reason"}


def version_readiness(conn: sqlite3.Connection) -> dict[str, Any]:
    """Is `version` usable as a control? The HONEST answer, per register.

    MEASURED: every row in every register holds `version = 1`. A control reading a
    constant controls nothing, so a version update must ride on `is_active`. This
    reports numbers rather than asserting a conclusion, because the day a register
    starts writing real versions the report changes by itself.
    """
    out: list[dict[str, Any]] = []
    for t in registers(conn):
        cols = _cols(conn, t)
        if "version" not in cols:
            continue
        r = conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT version) distinct_versions, "
            "MIN(version) mn, MAX(version) mx, SUM(version=1) ones FROM %s" % t
        ).fetchone()
        n = int(r[0] or 0)
        ones = int(r[4] or 0)
        # AN EMPTY TABLE IS NOT 'USABLE'. MEASURED BUG IN THIS MODULE'S FIRST
        # VERSION: `all_one = (n > 0 and ones == n)` made a register with ZERO rows
        # report `all_one=False` and therefore `usable_as_control=True` — claiming a
        # control works on no data. An empty register is UNKNOWN, not usable.
        if n == 0:
            all_one: bool | None = None
            usable = False
            why = "no rows: whether the column can carry versions is UNKNOWN"
        else:
            all_one = (ones == n)
            usable = not all_one
            why = ("every row is version=1, so a version-driven update has nothing "
                   "to distinguish" if all_one
                   else "the column carries more than one value")
        out.append({"register": t, "rows": n,
                    "distinct_versions": int(r[1] or 0),
                    "min": r[2], "max": r[3], "all_one": all_one,
                    "usable_as_control": usable, "why": why})
    usable = [x["register"] for x in out if x["usable_as_control"]]
    return {"ok": True, "registers": out, "declared": len(out),
            "usable_as_control": usable,
            "verdict": ("VERSION_IS_DECLARED_NOT_POPULATED"
                        if not usable else "VERSION_CARRIES_A_DISTINCTION"),
            "rule": ("until a register writes real versions, a version update rides "
                     "on is_active: retire the old row, activate the new"),
            "would_be_red_if": "a version update is driven by a constant column"}


def name_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many register NAME-KEYS are visible to the terminology ONE DOOR.

    A NUMBER, because this is the measurement that decides whether cross-register
    management can be built on `terminology_registry` today. MEASURED before: 2.1%.
    """
    import terminology_alias as ta

    try:
        terms = {str(r["term_key"]) for r in conn.execute(
            "SELECT term_key FROM terminology_registry")}
    except sqlite3.OperationalError:
        terms = set()
    per: list[dict[str, Any]] = []
    total = covered = 0
    skipped: list[dict[str, Any]] = []
    # A UNION, because the same name appears in several registers (a phase key is
    # counted by `register_fill_control`, `register_fill_phase` AND
    # `register_fill_run`). Summing per-register counts DOUBLE-COUNTS the same name
    # and inflates the denominator; the union is the number that answers "how much
    # of the naming is visible to ONE door".
    distinct: set[str] = set()
    distinct_covered: set[str] = set()
    for t in registers(conn):
        kc = key_column(conn, t)
        if not kc or kc not in _cols(conn, t):
            # A register with no NAME cannot have name coverage. Reported NAMED
            # rather than dropped, so the denominator is auditable.
            skipped.append({"register": t,
                            "why": NO_NAME_KEY.get(t, "no name key")})
            continue
        try:
            keys = [str(r[0]) for r in conn.execute("SELECT %s FROM %s" % (kc, t))]
        except sqlite3.OperationalError:
            continue
        hit = sum(1 for k in keys if k in terms)
        declared = 0
        for k in keys:
            try:
                if ta.resolve_name(conn, k).get("ok"):
                    declared += 1
            except Exception:
                pass
        per.append({"register": t, "key_column": kc, "keys": len(keys),
                    "in_terminology_registry": hit, "resolvable_via_door": declared})
        total += len(keys)
        covered += hit
        distinct.update(keys)
        distinct_covered.update(k for k in keys if k in terms)
    return {"ok": True, "per_registry": per, "total_keys": total,
            "covered": covered,
            "pct": (round(100.0 * covered / total, 1) if total else 0.0),
            "distinct_keys": len(distinct),
            "distinct_covered": len(distinct_covered),
            "distinct_pct": (round(100.0 * len(distinct_covered) / len(distinct), 1)
                             if distinct else 0.0),
            "excluded_no_name": skipped,
            "verdict": ("THE_REGISTERS_ARE_NOT_LINKED_BY_NAME"
                        if total and covered < total else "LINKED"),
            "why": ("management across registers must go through the ONE DOOR and "
                    "report the gap; it cannot be assumed from terminology_registry"),
            "would_be_red_if": "a cross-register value change assumes a name is "
                               "already registered"}


def set_lifecycle(conn: sqlite3.Connection, register: str, key: str, *,
                  key_column: str | None = None, is_active: int, cite_ref: str,
                  commit: bool = True) -> dict[str, Any]:
    """THE ONE WRITE PATH for the value model, applied uniformly to any register.

    `is_active=1` marks the CURRENT row, `is_active=0` marks the SUPERSEDED one.
    ONE `UPDATE` on ONE row — never an insert, never a delete, so history survives.

    THE CALLER SUPPLIES A NAME, NOT A SHAPE. `key_column` is DERIVED from the
    register when omitted, because the whole point of ONE model is that a caller
    applies it the same way to every register. A required `key_column` argument
    would push each register's internal shape back onto every call site — the very
    coupling the model removes.

    REFUSES:
      * `NOT_A_registry`     — the table is not in the register family.
      * `NO_LIFECYCLE`       — no `is_active`, with the NAMED reason.
      * `NO_SUCH_KEY_COLUMN` — an explicitly supplied column does not exist.
      * `NO_KEY_COLUMN`      — no key could be derived and none was supplied.
      * `UNCITED_CHANGE`     — a value change with no citation is uncheckable.
      * `NO_SUCH_ROW`        — the key names nothing; changing nothing is not a change.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "reason": "UNCITED_CHANGE",
                "why": "no citation, no change: a lifecycle change is a claim"}
    if register not in registers(conn):
        return {"ok": False, "reason": "NOT_A_registry", "register": register}
    cols = _cols(conn, register)
    if "is_active" not in cols:
        return {"ok": False, "reason": "NO_LIFECYCLE", "register": register,
                "why": NOT_APPLICABLE.get(register, "no is_active column")}
    kc = key_column
    if kc is None:
        kc = key_column_for(conn, register)
        if kc is None:
            return {"ok": False, "reason": "NO_KEY_COLUMN", "register": register,
                    "why": NO_NAME_KEY.get(
                        register, "no %s/_key column could be derived" % register)}
    if kc not in cols:
        return {"ok": False, "reason": "NO_SUCH_KEY_COLUMN",
                "register": register, "key_column": kc}
    want = int(is_active)
    if want not in (VALUE_MODEL["old"], VALUE_MODEL["new"]):
        return {"ok": False, "reason": "BAD_VALUE", "is_active": is_active,
                "why": "the model has exactly two values: %s" % VALUE_MODEL["meaning"]}
    cur = conn.execute(
        "SELECT is_active FROM %s WHERE %s = ?" % (register, kc),
        (str(key),)).fetchone()
    if cur is None:
        return {"ok": False, "reason": "NO_SUCH_ROW", "register": register,
                "key_column": kc, "key": str(key)}
    was = int(cur[0]) if cur[0] is not None else None
    if was == want:
        return {"ok": True, "changed": False, "register": register, "key": str(key),
                "key_column": kc, "is_active": want,
                "why": "already at the requested value"}
    conn.execute(
        "UPDATE %s SET is_active = ? WHERE %s = ?" % (register, kc),
        (want, str(key)))
    if commit:
        conn.commit()
    return {"ok": True, "changed": True, "register": register,
            "key_column": kc, "key": str(key),
            "was": was, "is_active": want, "cite_ref": str(cite_ref).strip()}


def retire_supersede(conn: sqlite3.Connection, register: str, old_key: str,
                     new_key: str, *, key_column: str | None = None,
                     cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """A VERSION UPDATE, expressed as the user's rule, in ONE atomic change.

    old row -> `is_active=0`, new row -> `is_active=1`. The old row is KEPT, so the
    previous value stays readable and the change is traceable. This is what the
    `version` column cannot do today (MEASURED: all-one).

    Like `set_lifecycle`, the caller supplies NAMES and the key column is DERIVED,
    so one call shape works for every register.

    REFUSES `NEW_KEY_ABSENT`: a supersede must name something real. Marking a key
    current that does not exist would INVENT a value.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "reason": "UNCITED_CHANGE",
                "why": "no citation, no supersede"}
    if str(old_key) == str(new_key):
        return {"ok": False, "reason": "SAME_KEY",
                "why": "a supersede needs two different keys"}
    kc = key_column or key_column_for(conn, register)
    if not kc or kc not in _cols(conn, register):
        return {"ok": False, "reason": "NO_KEY_COLUMN", "register": register}
    r_new = conn.execute(
        "SELECT 1 FROM %s WHERE %s = ?" % (register, kc), (str(new_key),)).fetchone()
    if r_new is None:
        return {"ok": False, "reason": "NEW_KEY_ABSENT", "register": register,
                "new_key": str(new_key),
                "why": "a supersede must name an EXISTING new key"}
    r_old = conn.execute(
        "SELECT 1 FROM %s WHERE %s = ?" % (register, kc), (str(old_key),)).fetchone()
    if r_old is None:
        return {"ok": False, "reason": "OLD_KEY_ABSENT", "register": register,
                "old_key": str(old_key)}
    # ONE ATOMIC SIDE EFFECT: either BOTH sides move or NEITHER does, so a reader
    # can never observe a moment with no current row.
    #
    # A SAVEPOINT, NOT `BEGIN IMMEDIATE`. MEASURED BUG IN THIS FUNCTION'S FIRST
    # VERSION: it opened a transaction with BEGIN and, on failure, called
    # `conn.rollback()` — which in SQLite aborts the OUTERMOST transaction, so a
    # caller who had already began one would lose their OWN work, not just this
    # change. A savepoint scopes the rollback to exactly this change.
    sp = "register_value_supersede"
    conn.execute("SAVEPOINT %s" % sp)
    try:
        a = set_lifecycle(conn, register, old_key,
                          key_column=kc,
                          is_active=VALUE_MODEL["old"], cite_ref=cite_ref,
                          commit=False)
        if not a.get("ok"):
            conn.execute("ROLLBACK TO %s" % sp)
            conn.execute("RELEASE %s" % sp)
            return {"ok": False, "reason": "OLD_CHANGE_FAILED", "detail": a}
        b = set_lifecycle(conn, register, new_key,
                          key_column=kc,
                          is_active=VALUE_MODEL["new"], cite_ref=cite_ref,
                          commit=False)
        if not b.get("ok"):
            conn.execute("ROLLBACK TO %s" % sp)
            conn.execute("RELEASE %s" % sp)
            return {"ok": False, "reason": "NEW_CHANGE_FAILED", "detail": b}
    except Exception as e:
        conn.execute("ROLLBACK TO %s" % sp)
        conn.execute("RELEASE %s" % sp)
        return {"ok": False, "reason": "SUPERSEDE_FAILED",
                "why": "%s: %s" % (type(e).__name__, e)}
    conn.execute("RELEASE %s" % sp)
    if commit:
        conn.commit()
    return {"ok": True, "register": register, "key_column": kc,
            "old": {"key": str(old_key), "was": a.get("was"),
                    "is_active": VALUE_MODEL["old"]},
            "new": {"key": str(new_key), "was": b.get("was"),
                    "is_active": VALUE_MODEL["new"]},
            "cite_ref": str(cite_ref).strip(),
            "rule": VALUE_MODEL["rule"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", action="store_true",
                    help="which registers conform to the value model")
    ap.add_argument("--versions", action="store_true",
                    help="is `version` usable as a control, per register")
    ap.add_argument("--name-coverage", action="store_true",
                    help="how many register name-keys the ONE DOOR can see")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.versions:
            out = version_readiness(conn)
        elif a.name_coverage:
            out = name_coverage(conn)
        else:
            out = audit(conn)
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.versions:
        print("verdict: %s" % out["verdict"])
        print("rule   : %s" % out["rule"])
        for x in out["registers"]:
            print("   %-30s rows=%-5d distinct=%-3d all_one=%-5s usable=%s"
                  % (x["register"], x["rows"], x["distinct_versions"],
                     x["all_one"], x["usable_as_control"]))
    elif a.name_coverage:
        print("verdict: %s" % out["verdict"])
        print("coverage: %d of %d name-keys (%.1f%%)"
              % (out["covered"], out["total_keys"], out["pct"]))
        for x in out["per_registry"]:
            print("   %-30s keys=%-5d registered=%-4d via_door=%d"
                  % (x["register"], x["keys"], x["in_terminology_registry"],
                     x["resolvable_via_door"]))
    else:
        c = out["counts"]
        print("model  : %s = %s (old) / %s (new)"
              % (VALUE_MODEL["value_column"], VALUE_MODEL["old"], VALUE_MODEL["new"]))
        print("classes: conformant=%d exception=%d unexplained=%d (of %d)"
              % (c["conformant"], c["exception"], c["unexplained"], c["total"]))
        for r in out["registers"]:
            flag = {"conformant": "ok ", "exception": "exc",
                    "unexplained": "GAP"}[r["class"]]
            print("   %-3s %-32s rows=%-5s active=%-6s key=%s"
                  % (flag, r["register"], r["rows"], r["active"],
                     r["key_column"] or "-"))
        if out["unexplained"]:
            print()
            print("   GAPS needing a decision: %s" % out["unexplained"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
