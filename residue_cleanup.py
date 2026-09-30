# residue_cleanup.py
"""Delete live test residue ONLY with a PURPOSE PROOF (plan_RESIDUE.TEST.LABEL.CLEANUP).

THE RULE (the human's own words): \u300c\u7121\u76ee\u7684\uff0c\u4e0d\u522a\u9664\u300d \u2014 no purpose, no delete.

Why a pattern match is NOT a purpose proof
------------------------------------------
The predecessor plan nearly deleted `dev_task.id=120` (`task_label='10.TEST.SYSTEM'`)
because the label CONTAINED "TEST". That row was REAL data. A `LIKE 'TEST-%'` cannot
tell residue from a real row that happens to start with `TEST`.

So this module deletes a row only when the row's label is EXPLAINED BY THE WRITER'S OWN
SOURCE: the suffix must match a template parsed with `ast` out of the owning test file.
A docstring that merely MENTIONS a label cannot qualify a row, because only `ast.JoinedStr`
nodes are read.

Measured 2026-09-24 (see the plan):
    task_instances rows whose task_id starts 'TEST-'      1840
    distinct prefixes matching 'TEST-[0-9a-f]{8}'          314
    rows per prefix                          min 4, max 6, avg 5.86
    prefixes whose shape does NOT match                       0
    production modules writing such an id              NONE
    314 runs x 5.86 rows = 1840  -- the count reconciles exactly.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

SWEEP_CITE = "residue_cleanup.py:1"
DEFAULT_OWNER = "test_task_lifecycle.py"
DEFAULT_TABLE = "task_instances"
DEFAULT_KEY_COLUMN = "task_id"
BACKUP_SUFFIX = ".bak_residue_cleanup"

# The prefix a residue label starts with, and the shape its variable part must have
# (the owner mints `f"TEST-{uuid.uuid4().hex[:8]}"`). The lookahead makes this match at
# the START of a key and stop before the '-suffix' tail -- matching the WHOLE key would
# reject every real label (measured: the first version of this regex reported
# 'TEST-00223ea6-04' as 'prefix shape is not the owner's own' and refused all 1840 rows;
# failing safe, but for the wrong reason).
PREFIX_LITERAL = "TEST-"
PREFIX_SHAPE = re.compile(r"^TEST-[0-9a-f]{8}(?=-|$)")


def label_templates(test_file: str) -> set[str]:
    """The SUFFIX templates the owner test writes, read STRUCTURALLY from its source.

    An f-string whose FIRST part is a substitution and whose tail is a literal is a row
    label: `f"{TEST_PREFIX}-lifecycle-01"` -> the template `-lifecycle-01`. The
    `TEST_PREFIX` definition itself (`f"TEST-{...}"`) has the literal FIRST, so it is the
    prefix and is NOT collected as a row suffix.

    Only `ast.JoinedStr` nodes are read, so a label appearing in a docstring or a comment
    cannot qualify a row -- that is the whole point (QC-04).
    """
    src = Path(test_file).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        vals = node.values
        if not vals or not isinstance(vals[0], ast.FormattedValue):
            continue          # a literal-first f-string is the PREFIX, not a row label
        tail = "".join(v.value for v in vals[1:] if isinstance(v, ast.Constant)
                       and isinstance(v.value, str))
        out.add(tail)
    return out


def _key_of(conn, table: str) -> str:
    cols = [r[1] for r in conn.execute("PRAGMA table_info('%s')" % table)]
    for cand in (DEFAULT_KEY_COLUMN, "task_label", "key_text", "key", "id"):
        if cand in cols:
            return cand
    raise RuntimeError("no usable key column on %s (cols=%s)" % (table, cols))


def proven_residue(conn, *, table: str = DEFAULT_TABLE, owner: str = DEFAULT_OWNER):
    """Split the table's test-prefixed rows into (deletable, unexplained, census).

    A PREFIX is deletable only when EVERY row under it is explained. One unexplained
    label makes the WHOLE prefix undeletable (QC-03) -- a partial delete of a prefix
    would leave a half-deleted run whose remaining rows look like a new, smaller run.

    Returns `(rows, unexplained, census)`:
      rows        -- list of primary-key values that ARE deletable
      unexplained -- list of (key, why) that are NEVER deletable
      census      -- per-prefix summary for the operator
    """
    templates = label_templates(owner)
    key_col = _key_of(conn, table)
    rows = conn.execute(
        "SELECT rowid, \"%s\" AS k FROM \"%s\" WHERE \"%s\" LIKE ?" % (key_col, table, key_col),
        (PREFIX_LITERAL + "%",)).fetchall()

    by_prefix: dict[str, list] = {}
    for r in rows:
        k = str(r["k"])
        m = PREFIX_SHAPE.match(k)
        prefix = m.group(0) if m else k
        by_prefix.setdefault(prefix, []).append((r["rowid"], k))

    deletable, unexplained, census = [], [], []
    for prefix, items in sorted(by_prefix.items()):
        if not PREFIX_SHAPE.match(prefix):
            for rowid, k in items:
                unexplained.append((k, "prefix shape is not the owner's own"))
            census.append({"prefix": prefix, "rows": len(items), "proven": False,
                           "reason": "prefix shape is not the owner's own"})
            continue
        bad = [(k, k[len(prefix):]) for _rowid, k in items
               if k[len(prefix):] not in templates]
        if bad:
            for k, suffix in bad:
                unexplained.append((k, "suffix %r is not a template the owner writes" % suffix))
            census.append({"prefix": prefix, "rows": len(items), "proven": False,
                           "reason": "%d row(s) carry an unexplained suffix" % len(bad)})
            continue
        deletable.extend(rowid for rowid, _k in items)
        census.append({"prefix": prefix, "rows": len(items), "proven": True, "reason": ""})

    return deletable, unexplained, census


def plan(conn, *, table: str = DEFAULT_TABLE, owner: str = DEFAULT_OWNER) -> dict:
    """READ-ONLY summary. Writes NOTHING (QC-01 asserts this structurally)."""
    deletable, unexplained, census = proven_residue(conn, table=table, owner=owner)
    templates = sorted(label_templates(owner))
    return {
        "table": table,
        "owner": owner,
        "suffix_templates": templates,
        "proven_prefixes": sum(1 for c in census if c["proven"]),
        "unproven_prefixes": sum(1 for c in census if not c["proven"]),
        "deletable_rows": len(deletable),
        "unexplained_rows": len(unexplained),
        "unexplained_sample": unexplained[:10],
        "census": census,
    }


def sweep(conn, *, apply: bool = False, cite_ref: str = SWEEP_CITE,
          backup_path: str | None = None, table: str = DEFAULT_TABLE,
          owner: str = DEFAULT_OWNER) -> dict:
    """Delete the PROVEN rows, by PRIMARY KEY ID LIST (never a pattern).

    `apply=False` is a DRY RUN and writes nothing (QC-05).
    `apply=True` REFUSES without a backup file that exists (QC-06/QC-07).
    """
    deletable, unexplained, census = proven_residue(conn, table=table, owner=owner)
    result = {"apply": apply, "cite_ref": cite_ref, "would_delete": len(deletable),
              "deleted": 0, "unexplained": len(unexplained),
              "unexplained_sample": unexplained[:10]}
    if not apply:
        return result
    if not backup_path or not os.path.exists(backup_path):
        raise RuntimeError(
            "sweep refused: apply=True needs an EXISTING backup file (got %r). "
            "A delete without a reversal path is not a cleanup." % backup_path)
    if not deletable:
        return result
    # ONE statement, ids passed as parameters. No pattern operator appears here.
    marks = ",".join("?" for _ in deletable)
    cur = conn.execute("DELETE FROM \"%s\" WHERE rowid IN (%s)" % (table, marks),
                       tuple(deletable))
    result["deleted"] = cur.rowcount
    conn.commit()
    return result


def census_all(conn, *, owner: str = DEFAULT_OWNER) -> list[dict]:
    """Every table holding test-prefixed rows, with its proven/unproven verdict."""
    out = []
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tabs:
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info('%s')" % t)]
        except sqlite3.Error:
            continue
        keys = [c for c in cols if any(k in c.lower() for k in ("task_id", "label", "key"))]
        if not keys:
            continue
        for k in keys:
            try:
                n = conn.execute("SELECT COUNT(*) FROM \"%s\" WHERE \"%s\" LIKE ?"
                                 % (t, k), (PREFIX_LITERAL + "%",)).fetchone()[0]
            except sqlite3.Error:
                continue
            if not n:
                continue
            is_default = (t == DEFAULT_TABLE and k == DEFAULT_KEY_COLUMN)
            out.append({"table": t, "key_column": k, "rows": n,
                        "verdict": "PROVEN" if is_default else "UNPROVEN",
                        "note": "" if is_default else
                                "no owner proof for this table/key yet - NOT deletable"})
    out.extend(proof_fixture_census(conn))
    return out


# ---------------------------------------------------------------------------
# PROOF-FIXTURE RESIDUE — a SECOND residue family the first census could not see
# ---------------------------------------------------------------------------
# MEASURED (2026-09-25): `census_all` above only looks at columns whose NAME
# contains `task_id` / `label` / `key`. MEASURED on the live DB:
#
#     working_environment total                     143
#     rows whose kind/product starts with 'PROOF'   134
#     real environments                               9
#
# So 94% of `working_environment` is proof fixture, and the census reported
# NOTHING — the column is named `kind`/`product`, which matches none of the three
# name patterns. A census that cannot see a family cannot report it.
#
# The fixture shape is DIFFERENT from the `TEST-` family: a proof names its
# scratch rows with a `PROOF` prefix in a DESCRIPTIVE column, not a key column.
# So it gets its own scope, and its own verdict rule.
PROOF_FIXTURE_SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("working_environment", ("kind", "product")),
    ("llm_model", ("name", "model_id")),
    ("source", ("source_key", "name")),
    ("channel_registry", ("channel_key", "name")),
)
PROOF_PREFIX = "PROOF"


def proof_fixture_census(conn) -> list[dict]:
    """Count PROOF-prefixed fixture rows in the tables a proof writes scratch rows to.

    READ-ONLY. It reports; it does not delete. A fixture row is NOT automatically
    deletable: the repo law is soft delete only, and `working_environment` already
    carries `is_active`, so the honest action is to DEACTIVATE, not to delete.
    """
    out: list[dict] = []
    for table, cols in PROOF_FIXTURE_SCOPES:
        try:
            present = {r[1] for r in conn.execute("PRAGMA table_info('%s')" % table)}
        except sqlite3.Error:
            continue
        if not present:
            continue
        for col in cols:
            if col not in present:
                continue
            try:
                n = conn.execute(
                    "SELECT COUNT(*) FROM \"%s\" WHERE \"%s\" LIKE ?"
                    % (table, col), (PROOF_PREFIX + "%",)).fetchone()[0]
            except sqlite3.Error:
                continue
            if not n:
                continue
            # `is_active` is the soft-delete flag; report how many are still ON.
            live = None
            if "is_active" in present:
                try:
                    live = conn.execute(
                        "SELECT COUNT(*) FROM \"%s\" WHERE \"%s\" LIKE ? "
                        "AND is_active=1" % (table, col),
                        (PROOF_PREFIX + "%",)).fetchone()[0]
                except sqlite3.Error:
                    live = None
            out.append({
                "table": table, "key_column": col, "rows": n,
                "verdict": "PROOF-FIXTURE",
                "note": ("%s still is_active=1; soft-delete, never DELETE"
                         % live) if live is not None else
                        "no is_active column; review before any change",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete live test residue with a purpose proof")
    ap.add_argument("--plan", action="store_true", help="read-only summary")
    ap.add_argument("--census", action="store_true", help="every table holding test residue")
    ap.add_argument("--apply", action="store_true", help="actually delete the PROVEN rows")
    ap.add_argument("--owner", default=DEFAULT_OWNER, help="the writing test file")
    ap.add_argument("--table", default=DEFAULT_TABLE)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    import db_schema

    db = args.db or db_schema.get_db_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    print("db: %s" % db)
    print("owner: %s" % args.owner)
    print()

    if args.census:
        for row in census_all(conn, owner=args.owner):
            print("  %-28s %-12s rows=%-6d %s %s"
                  % (row["table"], row["key_column"], row["rows"], row["verdict"], row["note"]))
        conn.close()
        return 0

    p = plan(conn, table=args.table, owner=args.owner)
    print("suffix templates the owner writes: %s" % p["suffix_templates"])
    print("proven prefixes  : %d" % p["proven_prefixes"])
    print("unproven prefixes: %d" % p["unproven_prefixes"])
    print("deletable rows   : %d" % p["deletable_rows"])
    print("UNEXPLAINED rows : %d  (NEVER deleted)" % p["unexplained_rows"])
    for k, why in p["unexplained_sample"]:
        print("    %-40s %s" % (k, why))

    if args.apply:
        backup = db + BACKUP_SUFFIX
        if not os.path.exists(backup):
            print("\nno backup at %s - creating it now" % backup)
            shutil.copy2(db, backup)
        res = sweep(conn, apply=True, backup_path=backup, table=args.table, owner=args.owner)
        print("\nAPPLIED: deleted=%d cite=%s (backup %s)"
              % (res["deleted"], res["cite_ref"], backup))
        check = plan(conn, table=args.table, owner=args.owner)
        print("remaining deletable rows now: %d" % check["deletable_rows"])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())