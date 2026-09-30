# -*- coding: utf-8 -*-
"""split_skill_registry.py — split the TWO populations that both call themselves
"skill".

THE MEASURED DISEASE (not an opinion)
-------------------------------------
```
skill_registry.skill_key         7 rows: yes_no, tdd_verify, verdict_3line,
                                         test_json_verdict, mouse_spot_verify,
                                         ontology_relation, failure_classification
                                         -> VERDICT PARSERS + DIMENSION OWNERS
skill_prompt_ssot.skill_key     27 rows: citation_discipline, env_task_proof,
                                         systematic_debugging, ...
                                         -> SKILLS

skill_registry INTERSECT skill_prompt_ssot         = ['mouse_spot_verify']  (1 of 7)
skill_registry INTERSECT skill_contract_template   = EMPTY
```
One shared NAME, two disjoint populations, near-zero overlap. `skill_key_alias`
was proposed to bridge them, and it was WRONG: an alias table is only needed when
IDENTITY IS A NAME. Repair the identity and the alias has nothing to describe.

THE FIX (human chose option ii: TWO registers)
```
component_registry   the 7 rows, RENAMED. A component is a prompt COMPOSITION
                     target: a verdict shape (parser) or a dimension owner.
                     `wording_registry.skill_id` etc. keep pointing HERE — the
                     FK currently carries the truth, only the table name lies.
skill_registry       a NEW table in the SAME shape as channel_registry /
                     module_registry / db_table_registry / db_field_registry /
                     capability_registry:
                         {x}_id INTEGER PRIMARY KEY AUTOINCREMENT
                         {x}_key TEXT UNIQUE
                         + is_active, version, timestamps
                     `skill_id INTEGER` is the IDENTITY. `skill_key` is a
                     human-readable LABEL, never an identity.
```

WHY A PURE RENAME, AND NOT A REPOINT
------------------------------------
Measured: the FKs already point at the RIGHT ROWS.
```
prompt_registry  36 -> mouse_spot_verify   2 -> verdict_3line
study_registry    1 -> mouse_spot_verify   1 -> verdict_3line
wording_registry 10 -> mouse_spot_verify  10 -> ontology_relation
                  5 -> failure_classification
```
`yes_no`, `tdd_verify`, `test_json_verdict` are referenced by NOTHING — they are
pure output shapes. Repointing any of these would CHANGE MEANING, and a migration
that changes meaning while claiming to be a rename is not a migration. So the FK
values are LEFT EXACTLY AS THEY ARE; only the table NAME changes.

`mouse_spot_verify` appears in BOTH tables, and that is TRUE rather than a
conflict: it is a skill AND it is a composable component. `component_registry`
gains `skill_ref INTEGER` so that a component which IS a skill is linked BY ID
(the human's law) rather than by a shared name.

Run:  .\\.venv\\Scripts\\python.exe split_skill_registry.py [--apply]
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

DB = BASE / "agent.db"

MIGRATION = "split_skill_registry_v1"

# The NEW skill_registry: the SAME shape every other registry in this repo uses
# (channel_registry / module_registry / db_table_registry / db_field_registry /
# capability_registry). Copied, not invented.
skill_registry_DDL = """
CREATE TABLE IF NOT EXISTS skill_registry (
    skill_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key     TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    description   TEXT,
    output_schema TEXT,
    parser        TEXT,
    taxonomy_path TEXT,
    -- THE CAPABILITY TAGS. `skill_factor.applies_to` is matched against this
    -- column, so it is the field that DECIDES which factors apply to a skill.
    -- It is NOT `taxonomy_path`: that is a canonical ontology path with hard
    -- validation, not a tag. COMMA-SEPARATED, so one skill can be both `code`
    -- and `crud`. `NA` (never NULL) follows the no_null standard, so a
    -- surviving NULL means the standardiser did not run, which is a defect.
    capability_tags TEXT  NOT NULL DEFAULT 'NA',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version       TEXT    NOT NULL DEFAULT '1',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skill_registry_active
  ON skill_registry (is_active, skill_key);
"""

# Applied to the renamed table. The FK columns keep their CURRENT names so a
# rename stays a rename: `wording_registry.skill_id` now points at
# `component_registry.component_id`, and SQLite rewrites that clause for us on
# version >= 3.25 (measured: 3.50.4).
COMPONENT_EXTRA = [
    ("skill_ref", "INTEGER"),
]

# Append-only record of the migration, in the shape `legacy_id_map` established.
MIGRATION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    migration   TEXT    NOT NULL,
    detail_json TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


class SplitRefused(RuntimeError):
    pass


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def _columns(conn, t: str) -> list[str]:
    if not _table_exists(conn, t):
        return []
    return [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)]


def already_done(conn) -> bool:
    """The migration is recorded, OR the rename is already present."""
    if _table_exists(conn, "schema_migration_log"):
        if conn.execute("SELECT 1 FROM schema_migration_log WHERE migration=?",
                        (MIGRATION,)).fetchone():
            return True
    return _table_exists(conn, "component_registry")


def target_skill_keys(conn) -> list[str]:
    """The keys ADMITTED into skill_registry: only ones EVIDENCED as a skill.

    DEFECT CAUGHT BY THE FIRST DRY RUN, AND IT WAS MINE
    ---------------------------------------------------
    The first version of this function used the UNION of
    `skill_prompt_ssot.skill_key` and `skill_contract_template.skill_key`. That
    union was 39 keys. Measured, only 8 of the 19 contract rows hold a REAL skill
    name; the other 11 hold a **DERIVED SLUG** manufactured from the contract id:

        TBL.CODE_registry  -> table_code_registry
        CH.LOCAL_PC        -> channel_local_pc
        API.POST_TASKS_VALIDATE -> api_post_tasks_validate

    A slug is the ALIAS PROBLEM IN ANOTHER COSTUME: a name invented to stand in
    for an identity nobody resolved. Admitting the union would have inserted 11
    skills that DO NOT EXIST — the same "fill the slot so it is not empty" defect
    as `member_id`, committed by the migration that exists to remove it.

    THE RULE (strict, and based on an OBSERVABLE): a key is admitted only when
    something EVINCES it is a skill —
        (a) it has a `skill_prompt_ssot` row, or
        (b) it has a canonical `*.skill.md` on disk.
    A slug satisfies NEITHER, so it is refused. The 11 slug rows are NOT a
    migration problem: they are exactly the unresolved decisions, and their
    correct representation is **NULL skill_ref**, which an INTEGER FK states
    natively and a slug string cannot.
    """
    ssot = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT skill_key FROM skill_prompt_ssot") if r[0]}
    on_disk = set()
    for p in (BASE / "skills").rglob("*.skill.md"):
        stem = p.name[: -len(".skill.md")]
        if stem:
            on_disk.add(stem)
    evidenced = ssot | on_disk

    # DEFECT FIXED (measured, and it was MINE): the docstring above promised
    # "(a) OR (b)", but the code returned `ssot | ctr_evidenced` — `on_disk` was
    # COMPUTED AND THEN DISCARDED. So a skill with a canonical `.skill.md` and no
    # ssot row was silently refused. Measured: `skill_phone_100_judge` and
    # `deepseek_copy` have files on disk and were NEITHER in skill_registry NOR in
    # component_registry — a rule stated but not enforced, inside the migration
    # written to remove exactly that. The union below is the rule the docstring
    # describes.
    #
    # A contract key is admitted ONLY if it is evidenced as a skill too.
    ctr_evidenced = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT skill_key FROM skill_contract_template")
        if r[0] and str(r[0]) in evidenced}
    return sorted(evidenced | ctr_evidenced)


def refused_slugs(conn) -> list[str]:
    """Contract skill_keys NOT evidenced as a skill. These are NOT inserted.

    Returned rather than ignored, so the refusal is REPORTED. A silent skip would
    make 11 unresolved names look like 11 resolved ones.
    """
    evidenced = set(target_skill_keys(conn))
    return sorted({str(r[0]) for r in conn.execute(
        "SELECT DISTINCT skill_key FROM skill_contract_template")
        if r[0] and str(r[0]) not in evidenced})


def backfill_skill_registry(conn) -> dict[str, Any]:
    """ADD the keys the evidence rule admits but the first run MISSED.

    Idempotent and additive: it never removes a row and never rewrites one. This
    is the repair for the discarded-`on_disk` defect above, and it is a separate
    function so the original migration stays a faithful record of what it did.
    """
    have = {str(r[0]) for r in conn.execute(
        "SELECT skill_key FROM skill_registry")}
    want = set(target_skill_keys(conn))
    added = []
    for k in sorted(want - have):
        conn.execute(
            "INSERT OR IGNORE INTO skill_registry (skill_key, name, description, "
            "parser) VALUES (?,?,?,?)",
            (k, k.replace("_", " ").replace(".", " ").strip() or k,
             "added by the evidence rule: a canonical .skill.md exists on disk",
             "result_yes_no"))
        added.append(k)
    conn.commit()
    return {"added": added, "added_count": len(added),
            "total": conn.execute(
                "SELECT COUNT(*) FROM skill_registry").fetchone()[0]}


def plan(conn) -> dict[str, Any]:
    """What WOULD change. Read-only."""
    keys = target_skill_keys(conn)
    reg_keys = {str(r[0]) for r in conn.execute(
        "SELECT skill_key FROM skill_registry")} if _table_exists(
            conn, "skill_registry") else set()
    cols = _columns(conn, "skill_registry")
    fk_to_reg = []
    for t in [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        if t.startswith("sqlite_") or t == "skill_registry":
            continue
        for fk in conn.execute("PRAGMA foreign_key_list(%s)" % t):
            if fk[2] == "skill_registry":
                fk_to_reg.append({"table": t, "column": fk[3],
                                  "rows": conn.execute(
                                      "SELECT COUNT(*) FROM %s" % t
                                  ).fetchone()[0]})
    return {
        "already_done": already_done(conn),
        "sqlite": sqlite3.sqlite_version,
        "legacy_alter_table": conn.execute(
            "PRAGMA legacy_alter_table").fetchone()[0],
        "current_skill_registry_rows": len(reg_keys),
        "current_skill_registry_columns": cols,
        "new_shape": [c[1] for c in conn.execute(
            "PRAGMA table_info(skill_registry)")] if False else
            [l.split()[0] for l in skill_registry_DDL.splitlines()
             if l.strip() and not l.strip().startswith(("CREATE", ");", "ON ",
                                                        "CREATE INDEX"))],
        "fk_referencing_tables": fk_to_reg,
        "union_skill_keys": len(keys),
        "union_keys": keys,
        "ssot_only": sorted({str(r[0]) for r in conn.execute(
            "SELECT DISTINCT skill_key FROM skill_prompt_ssot")} - reg_keys),
        "refused_slug_keys": refused_slugs(conn),
        "refused_slug_count": len(refused_slugs(conn)),
        "note": ("A contract skill_key that is NOT evidenced as a skill is a "
                 "DERIVED SLUG and is NOT inserted. Those contracts get "
                 "skill_ref = NULL, which is the honest 'no skill owns this "
                 "YET' state and is exactly what the unresolved decisions "
                 "mean."),
    }


def apply(conn) -> dict[str, Any]:
    """Rename + create + populate, in ONE transaction. Idempotent."""
    if already_done(conn):
        return {"action": "already_applied", "migration": MIGRATION}

    if conn.execute("PRAGMA legacy_alter_table").fetchone()[0]:
        raise SplitRefused(
            "PRAGMA legacy_alter_table is ON: ALTER TABLE RENAME would NOT "
            "rewrite other tables' FK clauses, so the rename would silently "
            "orphan prompt_registry/study_registry/wording_registry. Refusing.")

    ver = tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2])
    if ver < (3, 25):
        raise SplitRefused("sqlite %s < 3.25: rename does not update FK clauses"
                           % sqlite3.sqlite_version)

    # Snapshot the component rows BEFORE the rename so the result can be checked
    # against what was actually there (not against what we assume was there).
    before = {int(r[0]): str(r[1]) for r in conn.execute(
        "SELECT skill_id, skill_key FROM skill_registry")}
    fk_before = {}
    for t in ("wording_registry", "prompt_registry", "study_registry"):
        if _table_exists(conn, t):
            fk_before[t] = conn.execute(
                "SELECT COUNT(*) FROM %s" % t).fetchone()[0]

    conn.execute(MIGRATION_LOG_DDL)

    # ---- 1. RENAME. SQLite rewrites the referencing FK clauses for us.
    conn.execute("ALTER TABLE skill_registry RENAME TO component_registry")
    for col, decl in COMPONENT_EXTRA:
        if col not in _columns(conn, "component_registry"):
            conn.execute("ALTER TABLE component_registry ADD COLUMN %s %s"
                         % (col, decl))

    # ---- 2. CREATE the real skill_registry in the standard registry shape.
    conn.executescript(skill_registry_DDL)

    # ---- 3. POPULATE from the COMPUTED union.
    keys = target_skill_keys(conn)
    inserted, kept = 0, 0
    for k in keys:
        name = k.replace("_", " ").replace(".", " ").strip() or k
        cur = conn.execute(
            "INSERT OR IGNORE INTO skill_registry (skill_key, name, description, "
            "parser) VALUES (?,?,?,?)",
            (k, name, "registered from skill_prompt_ssot UNION "
                      "skill_contract_template", "result_yes_no"))
        if cur.rowcount:
            inserted += 1
        else:
            kept += 1

    # ---- 4. LINK a component that IS a skill, BY ID (the law).
    linked = 0
    for sid, ckey in before.items():
        row = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                           (ckey,)).fetchone()
        if row:
            conn.execute("UPDATE component_registry SET skill_ref=? "
                         "WHERE skill_id=?", (int(row[0]), int(sid)))
            linked += 1

    # ---- 4b. LINK each CONTRACT to its skill BY ID. A slug resolves to NOTHING
    # and stays NULL, because the honest state for an unresolved classification is
    # "no skill", which an INTEGER column says natively.
    if "skill_ref" not in _columns(conn, "skill_contract_template"):
        conn.execute("ALTER TABLE skill_contract_template "
                     "ADD COLUMN skill_ref INTEGER")
    c_linked, c_none = 0, 0
    for r in conn.execute("SELECT contract_id, skill_key "
                          "FROM skill_contract_template").fetchall():
        row = conn.execute("SELECT skill_id FROM skill_registry "
                           "WHERE skill_key=?", (r["skill_key"],)).fetchone()
        if row:
            conn.execute("UPDATE skill_contract_template SET skill_ref=? "
                         "WHERE contract_id=?", (int(row[0]), r["contract_id"]))
            c_linked += 1
        else:
            conn.execute("UPDATE skill_contract_template SET skill_ref=NULL "
                         "WHERE contract_id=?", (r["contract_id"],))
            c_none += 1

    # A RESOLVED contract must have a skill_ref; a SLUG one must NOT. Asserted
    # both ways, because an always-NULL column would satisfy the second half
    # alone and look perfect.
    bad_resolved = conn.execute(
        "SELECT COUNT(*) FROM skill_contract_template "
        "WHERE skill_key IN (SELECT skill_key FROM skill_registry) "
        "AND skill_ref IS NULL").fetchone()[0]
    bad_slug = conn.execute(
        "SELECT COUNT(*) FROM skill_contract_template "
        "WHERE skill_key NOT IN (SELECT skill_key FROM skill_registry) "
        "AND skill_ref IS NOT NULL").fetchone()[0]
    if bad_resolved or bad_slug:
        raise SplitRefused(
            "contract->skill link inconsistent: %d resolved-with-NULL, %d "
            "slug-with-a-link" % (bad_resolved, bad_slug))

    # ---- 5. Verify BEFORE committing. A migration that commits and then checks
    # is a migration that has already damaged the data.
    after = {int(r[0]): str(r[1]) for r in conn.execute(
        "SELECT skill_id, skill_key FROM component_registry")}
    if after != before:
        raise SplitRefused(
            "the renamed table lost or changed rows: before=%d after=%d "
            "(missing=%s)"
            % (len(before), len(after),
               {k: v for k, v in before.items() if after.get(k) != v}))
    fk_after = {}
    for t in fk_before:
        fk_after[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    if fk_after != fk_before:
        raise SplitRefused("a referencing table changed row count: %s -> %s"
                           % (fk_before, fk_after))

    detail = {
        "components_moved": len(before),
        "skills_inserted": inserted,
        "skills_already_present": kept,
        "components_linked_to_a_skill": linked,
        "contracts_linked_to_a_skill": c_linked,
        "contracts_left_with_NO_skill": c_none,
        "refused_slug_keys": refused_slugs(conn),
        "fk_rows_unchanged": fk_before,
        "sqlite": sqlite3.sqlite_version,
    }
    conn.execute("INSERT INTO schema_migration_log (migration, detail_json) "
                 "VALUES (?,?)", (MIGRATION, json.dumps(detail, sort_keys=True)))
    conn.commit()

    return {"action": "applied", "migration": MIGRATION, **detail}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        p = plan(conn)
        if not args.apply:
            print(json.dumps(p, indent=2, ensure_ascii=False))
            print("\n(dry run — pass --apply to migrate)")
            return 0
        out = apply(conn)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())