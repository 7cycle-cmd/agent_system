# -*- coding: utf-8 -*-
"""definition_from_evidence.py — a definition IS the facts, so a machine compares it.

THE HUMAN (2026-09-27), verbatim:

    "name or terminotlogy by evidence!! how to definition = respresentative
    compare not by human reason, logic generator can help easy!!"

THE IDEA, stated so it can be checked
-------------------------------------
A definition is a REPRESENTATION of what the database shows about an object. If
that is true, then:

  * the definition is DERIVABLE — `describe(facts_for(name))`, no human, no LLM;
  * two definitions are COMPARABLE — compare their FACTS, not their prose;
  * a definition can be WRONG in a checkable way — it claims something the facts
    do not show.

MEASURED, and this is why it matters: `terminology_registry` held BOTH shapes.

    proof_run           "The CURRENT name 'proof_run', proven by `sqlite_master`…"
                        cite: measured: sqlite_master table 'proof_run'   <- EVIDENCE
    channel_registry    "A channel registry is a database or directory that stores
                        information about av related things."
                        cite: register_fill.py:118                        <- PROSE

The prose one has NO fact behind it: nothing can contradict it, so nothing can
verify it. That is the defect this module removes — not by writing better prose,
but by making the definition BE the facts.

WHAT IT REUSES (no second answer to any question)
-------------------------------------------------
* the object's existence and kind -> `sqlite_master` (the DB), the SAME source
  `rename_all.provenance_for` cites as `measured: sqlite_master table 'x'`.
* the columns  -> `PRAGMA table_info`
* the edges    -> `PRAGMA foreign_key_list`
* the count    -> `SELECT COUNT(*)`
Every field is read. None is invented, and none is a literal in this file.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def facts_for(conn: sqlite3.Connection, name: str) -> dict:
    """Every fact the database holds about the object `name`.

    THE FIELDS ARE READ, NEVER ASSUMED. `exists` is read from `sqlite_master`, so a
    name that names nothing returns `exists=False` rather than an empty fact set --
    "the table has no columns" and "there is no table" are different answers, and
    conflating them is the defect the repo already names.

    Returns `{ok, name, exists, kind, columns, row_count, foreign_keys, indexes,
    cite}`. `ok=False` only when the name does not exist, with the reason.
    """
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "name": n, "exists": False,
                "why": "an empty name names nothing"}
    # ---- 1. does it EXIST, and what is it? (the DB is the source) ----------
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ? OR lower(name) = lower(?)",
        (n, n)).fetchone()
    if not row:
        return {"ok": False, "name": n, "exists": False,
                "why": "no sqlite object named %r — a definition for a thing that "
                       "does not exist cannot be checked" % n,
                "cite": "measured: sqlite_master has no object %r" % n}
    kind = str(row[0])  # 'table' | 'view' | 'index' | 'trigger'
    # The name AS STORED, so the definition names the real object even when the
    # caller's spelling differed in case.
    real = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = ? OR lower(name) = lower(?)",
        (n, n)).fetchone()[0]
    real = str(real)

    # ---- 2. the columns (PRAGMA, the DB's own description) ----------------
    cols = []
    try:
        for c in conn.execute("PRAGMA table_info(%s)" % real):
            cols.append({"name": str(c["name"]), "type": str(c["type"] or ""),
                         "notnull": int(c["notnull"] or 0),
                         "pk": int(c["pk"] or 0)})
    except sqlite3.Error:
        cols = []

    # ---- 3. the edges -----------------------------------------------------
    fks = []
    try:
        for f in conn.execute("PRAGMA foreign_key_list(%s)" % real):
            fks.append({"from": str(f["from"]), "to_table": str(f["table"]),
                        "to_col": str(f["to"] or "")})
    except sqlite3.Error:
        fks = []

    # ---- 4. the indexes ---------------------------------------------------
    idx = []
    try:
        for i in conn.execute("PRAGMA index_list(%s)" % real):
            idx.append({"name": str(i["name"]), "unique": int(i["unique"] or 0)})
    except sqlite3.Error:
        idx = []

    # ---- 5. the row count. A VIEW can be counted too; an INDEX cannot. ----
    count = None
    if kind in ("table", "view"):
        try:
            count = int(conn.execute("SELECT COUNT(*) FROM %s" % real).fetchone()[0])
        except sqlite3.Error:
            count = None

    return {"ok": True, "name": real, "exists": True, "kind": kind,
            "columns": cols, "row_count": count, "foreign_keys": fks,
            "indexes": idx,
            "cite": "measured: sqlite_master %s %r" % (kind, real)}


def describe(facts: dict) -> str:
    """The REPRESENTATIVE definition, built ONLY from the facts.

    A PURE function: the same facts give a byte-identical string, so two
    definitions are comparable by comparing their facts. That purity is the whole
    point -- it is what makes "compare, not reason" true.

    Every clause names a field that `facts_for` READ. There is no adjective, no
    purpose, and no "used for" phrasing, because the database does not know a
    purpose and a generated sentence must not invent one.
    """
    if not facts.get("ok"):
        return ""
    kind = facts["kind"]
    name = facts["name"]
    parts = ["%s %r" % (kind.capitalize(), name)]

    # ---- THE WORD CASE. A word is not a table, so it has no columns. --------
    #
    # NO COUNT AND NO NAME LIST APPEAR HERE, DELIBERATELY. MEASURED, and it is the
    # repo's own law: "a check that pins a NUMBER a legitimate operation moves is
    # STALE". Both the use count and the set of names MOVE whenever a term is
    # added, so writing either into a definition would make it wrong on the next
    # registration — the exact defect `describe` already avoids for `row_count`.
    # The LIVE count is reported by the caller; the definition states only what the
    # word IS.
    if kind == "word":
        reg = facts.get("registered_as") or []
        if reg:
            parts.append("is a registered word of the system vocabulary (%s)"
                         % ", ".join(reg))
        else:
            parts.append("appears in the register's names but is NOT itself a "
                         "registered word")
        eq = facts.get("equivalent_spellings") or []
        if eq:
            parts.append("has an equivalent spelling: %s" % ", ".join(eq))
        senses = facts.get("senses") or []
        if len(senses) > 1:
            parts.append("carries %d different definitions, one per place it is "
                         "declared (%s) — a word with two meanings"
                         % (len(senses), ", ".join(s["where"] for s in senses)))
        return "; ".join(parts) + "."

    cols = facts.get("columns") or []
    if cols:
        parts.append("has %d column(s)" % len(cols))
    else:
        parts.append("has no declared columns")

    pk = [c["name"] for c in cols if c.get("pk")]
    if pk:
        parts.append("primary key: %s" % ", ".join(sorted(pk)))

    # THE ROW COUNT IS DELIBERATELY NOT WRITTEN INTO THE DEFINITION.
    # MEASURED DEFECT IN MY OWN FIRST VERSION, and it is the repo's own law:
    # "a check that pins a NUMBER a legitimate operation moves is STALE".
    # `holds 12 row(s)` is a fact that goes WRONG on the next INSERT, so 97
    # definitions written from this function were wrong the moment a row was added.
    # The count is reported LIVE by the runner (`_run_definition_evidence.py`) instead,
    # which is where a number that moves belongs. The column count STAYS: it moves only
    # on a schema migration, which is an architecture change, not an ordinary write.
    notnull = sorted(c["name"] for c in cols if c.get("notnull") and not c.get("pk"))
    if notnull:
        parts.append("required columns: %s" % ", ".join(notnull))

    fks = facts.get("foreign_keys") or []
    if fks:
        parts.append("references: %s" % ", ".join(
            sorted("%s -> %s.%s" % (f["from"], f["to_table"], f["to_col"])
                   for f in fks)))
    else:
        parts.append("references: none")

    uniq = sorted(i["name"] for i in (facts.get("indexes") or []) if i.get("unique"))
    if uniq:
        parts.append("unique index(es): %s" % ", ".join(uniq))

    return "; ".join(parts) + "."


def definition_for(conn: sqlite3.Connection, name: str) -> dict:
    """The facts AND the definition, so a caller can check one against the other.

    Returns `facts_for()`'s dict plus `definition` and `cite`. `ok=False` when the
    object does not exist, and the definition is then the empty string -- NOT prose
    about a missing thing.
    """
    f = facts_for(conn, name)
    f["definition"] = describe(f) if f.get("ok") else ""
    return f


def unsupported_claims(definition: str, facts: dict) -> list[str]:
    """Which FACTS the definition would need but the fact set does not have.

    THIS IS THE COMPARISON, MADE WITHOUT OPINION. A definition of the EVIDENCE shape
    is a list of claims about the same fields `facts_for` reads; so a claim that
    names a field the facts do not carry is unsupported, and it is found by LOOKING,
    not by asking a model.

    MEASURED LIMIT, stated so it cannot be over-read: this detects an
    EVIDENCE-SHAPED definition that disagrees with the data. It CANNOT judge prose
    (`"a database or directory that stores information about av related things"` has
    no field to look up), and it says so by returning a `PROSE` marker rather than a
    silent empty list. The prose case is what the 7B second door is for.
    """
    if not facts.get("ok"):
        return ["OBJECT_MISSING"]
    d = str(definition or "")
    low = d.lower()
    out: list[str] = []
    # A definition that mentions NO field of the object is not describing it.
    cols = {str(c["name"]).lower() for c in (facts.get("columns") or [])}
    cols.add("column")
    if not any(c in low for c in cols) and "row" not in low:
        out.append("PROSE")
    # An EVIDENCE-shaped claim about a count must agree with the count.
    import re
    m = re.search(r"(\d+)\s*row", low)
    if m and facts.get("row_count") is not None:
        if int(m.group(1)) != int(facts["row_count"]):
            out.append("ROW_COUNT_MISMATCH:%s!=%s"
                       % (m.group(1), facts["row_count"]))
    m = re.search(r"(\d+)\s*column", low)
    if m and (facts.get("columns")):
        if int(m.group(1)) != len(facts["columns"]):
            out.append("COLUMN_COUNT_MISMATCH:%s!=%s"
                       % (m.group(1), len(facts["columns"])))
    return out


def names_a_real_value(conn: sqlite3.Connection, facts: dict,
                       definition: str) -> list[str]:
    """Which ACTUAL VALUES of the object the definition names. `[]` when none.

    WHY THIS EXISTS, MEASURED (2026-09-27). `unsupported_claims` returns `PROSE` for
    any definition that does not name a COLUMN, and that flag was about to authorise
    replacing 109 definitions. MEASURED over those 109:

        name a REAL VALUE that exists in the table : **11**   <- DOMAIN KNOWLEDGE
        name nothing checkable                     : **98**

    A sample of the 11 shows what would have been DESTROYED:

        catalog                 "A skill LIBRARY shelf: which kind of work a skill
                                 belongs to for FINDING it (core / db_schema / ...)"
        tdd_type                "…such as unit / integration…"
        route_registry          "…routing information…"
        plan_session_log        "A record of events during a planning session…"

    Those say something a schema dump CANNOT: what the rows MEAN. Replacing them
    with `Table 'catalog'; has 8 column(s)…` would have traded a small problem
    (uncheckable prose) for a LARGE one (knowledge destroyed) — which is worse than
    doing nothing.

    THE CHECK IS CONSERVATIVE ON PURPOSE: a substring match over the object's own
    distinct values. A false positive (a common word that happens to be a value)
    only ever PROTECTS a definition from replacement, never causes one. A guard that
    can only under-replace fails safe; a guard that can over-replace does not.
    """
    if not facts.get("ok"):
        return []
    low = str(definition or "").lower()
    hits: list[str] = []
    # Only the first few columns, and a bounded value scan: the point is to detect
    # the presence of DOMAIN content, not to exhaustively match every value.
    for c in (facts.get("columns") or [])[:8]:
        cn = str(c["name"])
        try:
            vals = [r[0] for r in conn.execute(
                "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL LIMIT 24"
                % (cn, facts["name"], cn))]
        except sqlite3.Error:
            continue
        for v in vals:
            s = str(v)
            # MEASURED DEFECT IN MY FIRST VERSION: a bare substring test with a
            # `len >= 3` floor MISSED `catalog`, whose definition names `ui` and `qa`
            # -- and `UI` / `QA` ARE real values in that table, but only 2 chars, so
            # the floor skipped them. Lowering the floor alone would make `NA` match
            # inside `planning`, so the test is a WORD-BOUNDARY match instead of a
            # substring: short values become usable WITHOUT matching inside a word.
            if len(s) >= 2 and re.search(r"(?<![a-z0-9])%s(?![a-z0-9])"
                                         % re.escape(s.lower()), low):
                hits.append("%s=%s" % (cn, s))
                break
    return hits


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", help="the object to describe")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        r = definition_for(conn, a.name)
        if not r.get("ok"):
            print("NO FACTS:", r.get("why"))
            return 1
        print("OBJECT : %s (%s)" % (r["name"], r["kind"]))
        print("CITE   : %s" % r["cite"])
        print("FACTS  : %d columns, %s rows, %d fk, %d index"
              % (len(r["columns"]), r["row_count"],
                 len(r["foreign_keys"]), len(r["indexes"])))
        print("DEF    : %s" % r["definition"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
