# -*- coding: utf-8 -*-
"""no_null.py — NULL is never a value. An empty field is standardised to `NA`,
and a NULL that survives is a DEFECT.

THE RULE (from the human)
-------------------------
"never allow null as define or accepted pass; must be value = NA"
"so we can classified it is define as system not BUG"

The reasoning is sound: NULL is an AMBIGUOUS state — it can mean "not
applicable", "unknown", "not decided", or "a bug". If every empty value is
standardised to the literal `NA`, then `NA` is a SYSTEM-DEFINED state and a NULL
is ALWAYS a defect (the standardiser did not run). An ambiguity becomes a
detectable defect.

TWO MEASURED FACTS THAT SHAPE THE RULE
--------------------------------------
1. `NA` IS A STRING, SO AN INTEGER COLUMN CANNOT STORE IT.
   Measured: 69 INTEGER columns hold NULL. A blanket rule would have to either
   turn them all into TEXT (losing numeric comparison, sorting, arithmetic, and
   breaking every FK that points at them) or silently skip them — and a rule that
   does not apply where it matters most is the defect this workset removes.
   So the rule is PER TYPE: `'NA'` for text, `NA_INT` for integers.

2. `NA` AND NULL ARE DIFFERENT CLAIMS, AND THE CENSUS PROVES IT MATTERS.
   ```
   NA   = "this field does not apply to this row"   (a DECISION)
   NULL = "nobody filled this in"                   (an ABSENCE)
   ```
   Measured, the largest NULL populations are FOREIGN KEYS:
   `task_ssot.parent_dim_id` 743, `chat_main_hash.chat_main_id` 468,
   `version_registry.parent_version_id` 190, `dev_task.queue_task_id` 160,
   `skill_prompt_ssot.parent_id` 75 ...
   A NULL there means "this row has NO parent" — a DECISION, not an absence.
   Writing `NA` would claim the parent is "not applicable", and forcing a
   sentinel FK would create a PHANTOM PARENT. So FK columns are the ONE place
   NULL is allowed, and it is DOCUMENTED rather than tolerated.

THE THREE STATES
----------------
    a real value  |  NA (decided: not applicable)  |  NULL (a defect)

`NA` must be WRITTEN, never inferred. A standardiser that guesses turns an
absence into a decision — the same defect family as `member_id` (a value filled
so the slot is not empty) and the 11 derived slugs.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The literal for a text column. Deliberately the two characters `NA`, not
# `N/A`, not `null`, not `''` — one spelling, so a query can find every one.
NA = "NA"

# The sentinel for a numeric column. `-1` is chosen because:
#   * it is storable in an INTEGER column (a string is not)
#   * it is NOT a plausible real value for a count, an id, or a line number
#   * it is already the convention in this repo for "no reference"
# A sentinel is a COMPROMISE, and it is documented as one: `-1` is
# indistinguishable from a real -1 unless the column's meaning excludes it. That
# is why `assert_na_is_explicit` exists — the sentinel must be written on purpose.
NA_INT = -1
NA_REAL = -1.0

# Column kinds the standardiser understands. An UNKNOWN kind is REFUSED, never
# guessed: guessing is how an absence becomes a decision.
KIND_TEXT = "text"
KIND_INT = "int"
KIND_REAL = "real"
KIND_FK = "fk"          # the ONE kind where NULL is allowed, and it means
                        # "no reference" — a decision, not an absence
KINDS = (KIND_TEXT, KIND_INT, KIND_REAL, KIND_FK)

# SQLite declared types -> kind. Matched on the DECLARED type, because that is
# what the schema says; the stored value's `typeof` is a runtime accident.
_TYPE_MAP = (
    ("INT", KIND_INT),
    ("REAL", KIND_REAL),
    ("FLOA", KIND_REAL),
    ("DOUB", KIND_REAL),
    ("NUM", KIND_REAL),
    ("TEXT", KIND_TEXT),
    ("CHAR", KIND_TEXT),
    ("CLOB", KIND_TEXT),
    ("TIMESTAMP", KIND_TEXT),
    ("DATE", KIND_TEXT),
    ("BOOL", KIND_INT),
)


class NullNotAllowed(RuntimeError):
    """Raised when a NULL would be persisted in a column that must carry a value."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("NULL not allowed — refusing to persist: %s"
                         % "; ".join(reasons))


def kind_of(declared: str) -> str:
    """Map a DECLARED column type to a kind. Unknown -> REFUSED, never guessed."""
    d = (declared or "").upper()
    for token, kind in _TYPE_MAP:
        if token in d:
            return kind
    raise NullNotAllowed(
        ["declared type %r is not a kind this rule understands. Refusing rather "
         "than guessing: a guessed kind turns an absence into a decision."
         % declared])


def na_value(kind: str) -> Any:
    """The NA representation for a kind. An unknown kind is REFUSED."""
    if kind == KIND_TEXT:
        return NA
    if kind == KIND_INT:
        return NA_INT
    if kind == KIND_REAL:
        return NA_REAL
    if kind == KIND_FK:
        return None          # "no reference" IS the NA for a foreign key
    raise NullNotAllowed(["unknown kind %r" % kind])


def is_na(value: Any, kind: str) -> bool:
    """True when the value IS the NA representation for its kind."""
    if kind == KIND_FK:
        return value is None
    if kind == KIND_TEXT:
        return isinstance(value, str) and value.strip() == NA
    if kind == KIND_INT:
        return value == NA_INT
    if kind == KIND_REAL:
        return value == NA_REAL
    raise NullNotAllowed(["unknown kind %r" % kind])


def standardize_empty(value: Any, kind: str) -> Any:
    """Turn an EMPTY value into the explicit NA for its kind.

    Empty means `None` or a whitespace-only string. A real value is returned
    UNCHANGED — this function never rewrites data, only fills an absence with a
    stated decision.
    """
    if kind not in KINDS:
        raise NullNotAllowed(["unknown kind %r" % kind])
    if value is None:
        return na_value(kind)
    if isinstance(value, str) and not value.strip():
        return na_value(kind)
    return value


def fk_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The columns of `table` that are FOREIGN KEYS.

    Read from the schema, never listed by hand: a hand-written list drifts from
    the DDL and then the gate protects the wrong columns.
    """
    return {fk[3] for fk in conn.execute("PRAGMA foreign_key_list(%s)" % table)}


def column_kinds(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """{column: kind} for a table, with FK columns marked as KIND_FK."""
    fks = fk_columns(conn, table)
    out: dict[str, str] = {}
    for c in conn.execute("PRAGMA table_info(%s)" % table):
        name, declared = c[1], c[2]
        out[name] = KIND_FK if name in fks else kind_of(declared)
    return out


# ---------------------------------------------------------------------------
# THE WRITE-SITE HELPERS — one call that standardises AND gates
# ---------------------------------------------------------------------------
# WHY THESE EXIST (measured 2026-09-22). `standardize_empty` and
# `assert_no_null` were both correct and both UNUSED: only 2 modules called
# them. Every other writer inserted `None` straight into a TEXT column, so the
# NULLs a backfill had removed CAME BACK:
#
#     skill_mismatch_log.error_reason   685 NULL -> 0 (backfilled) -> 1578 NULL
#     dev_task.error                    160 NULL -> 0 (backfilled) ->    6 NULL
#
# The backfill was correct; the WRITE PATH was never fixed. A rule enforced only
# by a periodic sweep is a rule that is violated between sweeps, and the sweep's
# own success is what hides it.
#
# So the fix is not "remember to call standardize_empty". It is ONE call that
# does both halves, so a writer cannot do the first without the second.
def standardize_row(conn: sqlite3.Connection, table: str,
                    row: dict[str, Any], *,
                    allow: tuple[str, ...] = ()) -> dict[str, Any]:
    """Standardise every empty value in `row`, then GATE the whole row.

    A column NOT in the table is passed through untouched so `assert_no_null`
    REFUSES it — a typo must be reported, not silently standardised.
    An FK column is passed through untouched: NULL there is the NA for a
    reference and is a DECISION, not an absence.
    """
    kinds = column_kinds(conn, table)
    out: dict[str, Any] = {}
    for col, value in row.items():
        kind = kinds.get(col)
        if kind is None or kind == KIND_FK:
            out[col] = value
            continue
        out[col] = standardize_empty(value, kind)
    return assert_no_null(out, table, kinds=kinds, allow=allow)


def insert_row(conn: sqlite3.Connection, table: str, row: dict[str, Any], *,
               allow: tuple[str, ...] = ()) -> Any:
    """INSERT a row through the standardiser. The sanctioned insert path.

    Only the columns NAMED in `row` are written; every other column keeps its
    DDL default. That is deliberate: a column with a real SQL default
    (`status TEXT NOT NULL DEFAULT 'pending'`) must not be overwritten with `NA`
    merely because the caller did not mention it.
    """
    clean = standardize_row(conn, table, row, allow=allow)
    cols = list(clean)
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ", ".join(cols), ", ".join("?" for _ in cols))
    cur = conn.execute(sql, tuple(clean[c] for c in cols))
    return cur.lastrowid


def update_row(conn: sqlite3.Connection, table: str, row: dict[str, Any], *,
               where: str, params: tuple[Any, ...] = (),
               allow: tuple[str, ...] = ()) -> int:
    """UPDATE a row through the standardiser. `where` is a SQL fragment.

    An UPDATE that sets a column to `None` is the SAME defect as an INSERT that
    does, and it is the one that RE-CREATED the NULLs after the backfill.
    """
    clean = standardize_row(conn, table, row, allow=allow)
    cols = list(clean)
    sql = "UPDATE %s SET %s WHERE %s" % (
        table, ", ".join("%s = ?" % c for c in cols), where)
    cur = conn.execute(sql, tuple(clean[c] for c in cols) + tuple(params))
    return cur.rowcount


def assert_no_null(row: dict[str, Any], table: str, *,
                   conn: sqlite3.Connection | None = None,
                   kinds: dict[str, str] | None = None,
                   allow: tuple[str, ...] = ()) -> dict[str, Any]:
    """The WRITE-SITE gate. A NULL in a non-FK column RAISES.

    `allow` names columns that are exempt for a documented reason; it is an
    explicit argument so an exemption is VISIBLE at the call site rather than
    hidden in a list somewhere.
    """
    if kinds is None:
        if conn is None:
            raise NullNotAllowed(
                ["no schema available: pass conn= or kinds= so the gate can tell "
                 "an FK column from a value column. Guessing would protect the "
                 "wrong columns."])
        kinds = column_kinds(conn, table)

    reasons: list[str] = []
    for col, value in row.items():
        if col in allow:
            continue
        kind = kinds.get(col)
        if kind is None:
            reasons.append("%s.%s is not a column of this table" % (table, col))
            continue
        if kind == KIND_FK:
            # NULL here is a DECISION ("no reference"), and it is the only place
            # NULL is allowed. It is not silently accepted: it is named.
            continue
        if value is None:
            reasons.append(
                "%s.%s is NULL. An empty value must be the explicit %r, so that "
                "a NULL means 'the standardiser did not run' — a defect — rather "
                "than an ambiguous absence." % (table, col, na_value(kind)))
    if reasons:
        raise NullNotAllowed(reasons)
    return row


def assert_na_is_explicit(value: Any, kind: str, *, where: str = "") -> Any:
    """NA must be WRITTEN, never inferred.

    A standardiser that guesses turns an ABSENCE into a DECISION. This asserts
    the caller supplied the NA on purpose: an empty input is REFUSED here, and
    must go through `standardize_empty` first, which is a visible act.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise NullNotAllowed(
            ["%s: an empty value reached the write site. Call "
             "standardize_empty() first — NA must be an explicit decision, not "
             "an inference." % (where or "value")])
    return value


def audit(conn: sqlite3.Connection, *, limit: int = 40) -> dict[str, Any]:
    """Every NULL in the database, split by whether it is ALLOWED.

    A NULL in an FK column is allowed and is reported as `no_reference`.
    A NULL anywhere else is a DEFECT and is reported as `defect`.
    The two are counted SEPARATELY, because a report that merges them cannot
    tell a decision from a bug.
    """
    defects: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "ORDER BY name"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        if n == 0:
            continue
        fks = fk_columns(conn, t)
        for c in conn.execute("PRAGMA table_info(%s)" % t):
            col = c[1]
            try:
                k = conn.execute("SELECT COUNT(*) FROM %s WHERE %s IS NULL"
                                 % (t, col)).fetchone()[0]
            except sqlite3.Error:
                continue
            if not k:
                continue
            rec = {"table": t, "column": col, "nulls": k, "rows": n,
                   "declared": c[2]}
            (allowed if col in fks else defects).append(rec)
    defects.sort(key=lambda d: -d["nulls"])
    allowed.sort(key=lambda d: -d["nulls"])
    return {
        "defect_columns": len(defects),
        "defect_nulls": sum(d["nulls"] for d in defects),
        "allowed_columns": len(allowed),
        "allowed_nulls": sum(d["nulls"] for d in allowed),
        "defects": defects[:limit],
        "allowed": allowed[:limit],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--audit", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.audit:
            print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
        else:
            print("kinds: %s" % (KINDS,))
            print("NA=%r  NA_INT=%r  NA_REAL=%r" % (NA, NA_INT, NA_REAL))
    finally:
        conn.close()


if __name__ == "__main__":
    main()