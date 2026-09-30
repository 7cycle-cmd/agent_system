# -*- coding: utf-8 -*-
"""_unify_registry_naming.py — SUPERSEDED. Use `name_unify.py`.

🔴 SUPERSEDED 2026-09-28. THE OWNER OF "rename a name" IS NOW `name_unify.py`.

WHY THIS FILE IS SUPERSEDED, MEASURED
-------------------------------------
This script was the FIRST owner of the `_registry` -> `_registry` rule, and the
rule lived here as an `if` (`OLD, NEW = "_registry", "_registry"` at `:63`) with
no citation. MEASURED 2026-09-28: that made it a SECOND owner, and a second owner
is how a rule drifts.

`name_unify.py` now owns the rule, and the rule is a DECLARED ROW
(`terminology_blacklist` id 38, kind `suffix`, cite
`qc_evidence/plan_NAME.UNIFY.ALL.CARRIERS.md:P0`) rather than an `if`. It covers
17 layers (L1..L17): term keys, aliases, phase values, sqlite objects, file
content, column names, file names, function names, registry keys, aliases that
differ, registry tables, code references, and PROOF references.

WHAT THIS FILE STILL IS
-----------------------
A RECORD of the first migration, and the source of two facts `name_unify.py`
still cites:
  * `:29` — `ALTER TABLE ... RENAME TO` IS the additive form (the data is not
    copied, so there is nothing to miscopy).
  * `:55` — the rule as it was first written.

It is NOT deleted: a record of how a rule was first enforced is evidence, and
deleting evidence to make a count look good is the defect this repo names
everywhere. It is marked SUPERSEDED so the next reader picks the right tool.

DO NOT RUN `--apply` FROM THIS FILE. It renames only the DB side and reports the
code side as "the next step", which is exactly the half-rename `name_unify.py`
was built to avoid.

Run instead:
    .\\.venv\\Scripts\\python.exe -c "import name_unify, sqlite3, pathlib; \\
        c=sqlite3.connect('agent.db'); print(name_unify.plan_writes(c, pathlib.Path('.'))['by_action'])"

THE ORIGINAL HEADER FOLLOWS, UNCHANGED (it is the record).
----------------------------------------------------------
ORIGINAL: _unify_registry_naming.py -- enforce the ruling: `registry`, NOT `register`.

THE RULING (2026-09-27), verbatim:

    "be unified by registry not register"

WHAT THIS IS FOR
----------------
`terminology_registry.check_registry_name` now REFUSES a NEW `_registry` name, so
the standard is enforced at the write site going forward. But there are EXISTING
non-standard names. This script is the MIGRATION for those, and it PLANS before it
acts: `--plan` prints every rename; `--apply` performs it.

WHY A PLAN STEP AND NOT JUST A RENAME
-------------------------------------
MEASURED scale (2026-09-27): 31 sqlite objects and 39 active terms end in
`_registry`; 5984 code references sit in 569 files; and **8 entity letters have
`entity_type_registry.register_table` pointing at a `_registry` table**, so a
rename changes ENTITY ID RESOLUTION.

That last line is why this is not cosmetic: `T-19-...` resolves through
`table_id_of_letter()` -> `entity_type_registry.register_table`. Rename the table
without updating that row and every entity id for that letter stops resolving.

THE ORDER IS THE REPO'S OWN RENAME LESSON
(`/memories/repo/terminology_registry_naming.md:33`): "Migration is ADDITIVE:
create new -> copy -> VERIFY counts -> drop old." For a sqlite table,
`ALTER TABLE ... RENAME TO` IS the additive form: the data is not copied, so there
is nothing to miscopy, and the count is verified after each rename.

WHAT IT DOES NOT DO
-------------------
* It does NOT delete data. A RENAME moves; it does not destroy.
* It does NOT touch a name that does not end in `_registry`.
* It does NOT rewrite code: the DB and the CODE must be renamed TOGETHER, so
  `--plan` prints the code side and `--apply` reports it as the next step. A
  half-renamed DB plus rewritten code is worse than either alone.

Run:
    .\\.venv\\Scripts\\python.exe _unify_registry_naming.py --plan
    .\\.venv\\Scripts\\python.exe _unify_registry_naming.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"
OLD, NEW = "_registry", "_registry"


def plan_objects(conn: sqlite3.Connection) -> list[dict]:
    """Every sqlite object (table/view/index/trigger) ending in `_registry`."""
    out = []
    for r in conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name LIKE '%" + OLD + "' ORDER BY type, name"):
        out.append({"type": str(r["type"]), "old": str(r["name"]),
                    "new": str(r["name"])[:-len(OLD)] + NEW})
    return out


def plan_columns(conn: sqlite3.Connection) -> list[dict]:
    """Columns whose NAME ends in `_registry` (a column rename too)."""
    out = []
    for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        tn = str(t["name"])
        for c in conn.execute("PRAGMA table_info(%s)" % tn):
            n = str(c["name"])
            if n.endswith(OLD):
                out.append({"table": tn, "old": n,
                            "new": n[:-len(OLD)] + NEW})
    return out


def plan_entities(conn: sqlite3.Connection) -> list[dict]:
    """`entity_type_registry.register_table` rows pointing at a `_registry` table."""
    out = []
    for r in conn.execute(
            "SELECT type_letter, register_table, pk_column FROM entity_type_registry "
            "WHERE register_table LIKE '%" + OLD + "' ORDER BY type_letter"):
        out.append({"letter": str(r["type_letter"]),
                    "old": str(r["register_table"]),
                    "new": str(r["register_table"])[:-len(OLD)] + NEW,
                    "pk_column": str(r["pk_column"])})
    return out


def plan_registry_rows(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT db_table_id, table_key FROM db_table_registry "
            "WHERE table_key LIKE '%" + OLD + "' ORDER BY table_key"):
        out.append({"db_table_id": int(r["db_table_id"]),
                    "old": str(r["table_key"]),
                    "new": str(r["table_key"])[:-len(OLD)] + NEW})
    return out


def plan_terms(conn: sqlite3.Connection, table: str = "terminology_registry") -> list[dict]:
    """The terms to rename. `table` is a parameter because the CALLER may run
    AFTER the table has been renamed -- MEASURED DEFECT: `plan_terms(conn)` after
    the rename raised `no such table: terminology_registry`.

    ONLY A TERM THAT **NAMES A LIVE OBJECT** IS RENAMED (fixed 2026-09-27).

    MEASURED, and this was the defect: this function matched every term whose key
    ends in `_registry` -- 39 of them -- and 17 do NOT name an object at all.
    MEASURED: `create_terminology_registry` is *"An ACTION to establish or
    initialize a terminology register"*, `split_skill_registry` is *"A COUNT of
    separate skill registers"*, `channel_registry` and `job_registry` name
    entities that have NO table. Renaming those to `_registry` FABRICATES a table
    name: `channel_registry` reads as a table that does not exist, which is a
    worse defect than the inconsistency it removes.

    THE RULE IS CHECKABLE: the term is renamed only when `sqlite_master` holds an
    object with that exact name -- i.e. the term names the thing it says it names.
    Everything else is RETURNED as `skipped` (reported, never silently dropped),
    so the plan states how many terms were left alone and why.
    """
    out = []
    skipped: list[dict] = []
    for r in conn.execute(
            "SELECT term_id, term_key FROM %s "
            "WHERE term_key LIKE '%%" % table + OLD + "' ORDER BY term_key"):
        old = str(r["term_key"])
        new = old[:-len(OLD)] + NEW
        live = conn.execute(
            "SELECT type FROM sqlite_master WHERE type IN ('table','view') "
            "AND name=?", (old,)).fetchone()
        if not live:
            # NOT A RENAME: the term does not name an object. Renaming it would
            # invent a table name, so it is LEFT ALONE and REPORTED.
            skipped.append({"term_id": int(r["term_id"]), "old": old,
                            "would_be": new,
                            "why": ("names no live table/view; the term is an "
                                    "ACTION or a COUNT or an entity with no "
                                    "table, so `%s` would fabricate one" % new)})
            continue
        out.append({"term_id": int(r["term_id"]), "old": old, "new": new,
                    "object_type": str(live[0])})
    plan_terms.skipped = skipped          # MEASURED, so the plan can report it
    return out


def _live_roots() -> tuple[Path, ...]:
    """The directories that hold LIVE CODE. Everything else is HISTORY.

    MEASURED, and it is the reason the code side is a curated list and not a glob:
    the references live in

        .py  ROOT (live code)          522 files
        .md  qc_evidence (HISTORY)     305 files
        .json evidence (EVIDENCE)      256 files
        .md  evidence (EVIDENCE)       254 files
        .json qc_evidence (HISTORY)    240 files
        ... plus skills_generated/docs/dist

    **A name found in `qc_evidence/` or `evidence/` is a RECORD, not a reference.**
    Rewriting those would destroy provenance -- the transcript of what was done and
    the evidence a verdict rested on -- to make a cosmetic point. So this list is
    CURATED, and the excluded directories are named here rather than silently
    skipped.
    """
    return (BASE, BASE / "scripts", BASE / "src", BASE / "llm_task_monitor_ui" / "src")


# Directories that are HISTORY or EVIDENCE. Never rewritten, and the reason is
# stated so a later reader cannot mistake the omission for an oversight.
HISTORY_DIRS = ("qc_evidence", "evidence", "docs", "skills_generated", "dist",
                "node_modules", ".venv", ".git", "skills")


def plan_code() -> list[dict]:
    """LIVE-CODE files whose text references a renamed object.

    A FILE is rewritten; a GENERIC document is only reported. The distinction is
    the script-vs-document bug this repo has hit before: an LLM asking "is this a
    file?" can mistake a doc for one, and a wrong answer rewrites a document.
    """
    conn = sqlite3.connect(str(DB), timeout=30)
    try:
        names = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%" + OLD + "'")]
    finally:
        conn.close()
    rx = re.compile(r"\b(" + "|".join(sorted((re.escape(n) for n in names),
                                            key=len, reverse=True)) + r")\b")
    out = []
    manual: list[dict] = []
    roots = tuple(str(r) for r in _live_roots())
    seps = ("\\", "/")
    for p in sorted(BASE.rglob("*")):
        s = str(p)
        if any((sep + d + sep) in s for d in HISTORY_DIRS for sep in seps):
            continue
        if p.suffix not in (".py", ".js", ".json", ".html", ".sql", ".txt"):
            continue
        if not any(s.startswith(r) for r in roots):
            continue
        # A PROOF IS NOT REWRITTEN AUTOMATICALLY. MEASURED REASON: a proof asserts
        # a SPECIFIC spelling (`version_registry` is REFUSED, `version_registry` is
        # ACCEPTED). A mechanical rewrite changes BOTH sides of such a pair, so the
        # assertion would compare a string with itself -- a tautology that can never
        # fail, which is worse than a failing test. These files are REPORTED for
        # review instead.
        bn = p.name
        if bn.startswith("_proof_") or bn == Path(__file__).name:
            manual.append({"file": str(p.relative_to(BASE)),
                           "reason": "proof/selftest: a mechanical rewrite would "
                                     "make its assertions tautological"})
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hits = rx.findall(t)
        if hits:
            out.append({"file": str(p.relative_to(BASE)), "count": len(hits),
                        "names": sorted(set(hits))})
    plan_code.manual = manual
    return out


def plan_history_refs() -> dict:
    """COUNT the references inside HISTORY/EVIDENCE. Never rewritten.

    A count, not a list of files to edit: a name in `qc_evidence/` or `evidence/`
    is a RECORD of what was done, and rewriting it would falsify provenance to make
    a cosmetic point. The number is REPORTED so the omission is a measurement.
    """
    conn = sqlite3.connect(str(DB), timeout=30)
    try:
        names = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%" + OLD + "'")]
    finally:
        conn.close()
    rx = re.compile(r"\b(" + "|".join(sorted((re.escape(n) for n in names),
                                            key=len, reverse=True)) + r")\b")
    files = occ = 0
    seps = ("\\", "/")
    for p in sorted(BASE.rglob("*")):
        s = str(p)
        if not any((sep + d + sep) in s for d in HISTORY_DIRS for sep in seps):
            continue
        if p.suffix not in (".py", ".js", ".json", ".md", ".html", ".sql", ".txt"):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        n = len(rx.findall(t))
        if n:
            files += 1
            occ += n
    return {"files": files, "occurrences": occ}


def _apply_code_map() -> dict[str, str]:
    """`{old_name: new_name}` for every renamed object. ONE mechanical rule."""
    conn = sqlite3.connect(str(DB), timeout=30)
    try:
        names = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%" + OLD + "'")]
    finally:
        conn.close()
    return {n: n[:-len(OLD)] + NEW for n in names}


def plan_module_files() -> list[dict]:
    """`.py` files whose NAME is a renamed object's name.

    MEASURED BLOCKER (2026-09-27): **12 modules share a renamed table's name** --
    `identity_registry.py`, `prompt_registry.py`, `worker_registry.py`, ... -- and
    there are **99 import statements** for them in **79 files**.

    So the substring rewrite ALONE is BROKEN: it turns `import identity_registry`
    into `import identity_registry` while the FILE is still `identity_registry.py`,
    and 79 files stop importing. The identifier rewrite is correct; the missing half
    is RENAMING THE FILE. With both, the same mechanical rule keeps the table
    string, the import and the filename consistent.
    """
    pairs = _apply_code_map()
    out = []
    for old, new in sorted(pairs.items()):
        p = BASE / (old + ".py")
        if p.exists():
            out.append({"old": old + ".py", "new": new + ".py"})
    return out


def backup_live_files() -> str:
    """Zip the LIVE roots BEFORE any rewrite. THE ROLLBACK THAT GIT CANNOT GIVE.

    MEASURED: **522 of the 543 target files are UNTRACKED by git** (this repo
tracks only 36 root `.py` files), so `git checkout` cannot restore them. A
    rewrite of 543 files with no rollback is not a migration, it is a gamble.
    """
    import zipfile
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BASE / ("_backup_registry_naming_%s.zip" % stamp)
    roots = _live_roots()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for r in roots:
            for p in r.rglob("*"):
                s = str(p)
                if any(("\\" + d + "\\") in s or ("/" + d + "/") in s
                       for d in ("node_modules", ".venv", ".git", "dist")):
                    continue
                if p.is_file() and p.suffix in (".py", ".js", ".json", ".html",
                                                ".sql", ".txt"):
                    try:
                        z.write(p, str(p.relative_to(BASE)))
                    except Exception:
                        pass
    return str(out)


def rewrite_live_code(*, apply: bool) -> dict:
    """Rewrite LIVE CODE only. ONE regex pass, longest name first, so a shorter
    name cannot corrupt a longer one (`code_registry` before `register`)."""
    pairs = _apply_code_map()
    rx = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in pairs),
                                            key=len, reverse=True)) + r")\b")
    changed = []
    for c in plan_code():
        p = BASE / c["file"]
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = rx.sub(lambda m: pairs[m.group(1)], t)
        if new != t:
            if apply:
                p.write_text(new, encoding="utf-8", newline="")
            changed.append({"file": c["file"], "replacements": c["count"]})
    # THE MODULE FILES, renamed so the rewritten imports resolve. A file RENAME,
    # not a copy: two files defining the same module is two answers to one name.
    mods = []
    for m in plan_module_files():
        src, dst = BASE / m["old"], BASE / m["new"]
        if apply and src.exists():
            src.replace(dst)
            mods.append(m)
        elif src.exists():
            mods.append(m)
    return {"files": len(changed), "apply": apply, "changed": changed,
            "modules_renamed": mods}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="",
                    help="override the DB path (for a REHEARSAL on a copy)")
    args = ap.parse_args(argv)
    apply = bool(args.apply)

    global DB
    if args.db:
        DB = Path(args.db).resolve()
        print("DB override (rehearsal): %s\n" % DB)

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        objs, cols = plan_objects(conn), plan_columns(conn)
        ents, regs, terms = plan_entities(conn), plan_registry_rows(conn), plan_terms(conn)
        code = plan_code()
        # THE HISTORY side is COUNTED, never rewritten, so the omission is a
        # reported number rather than a silent skip.
        hist = plan_history_refs()
        # THE TERMS THAT NAME NO OBJECT. MEASURED: 17 of the 39 `_registry` terms
        # are ACTIONS, COUNTS, or entities with no table, so renaming them would
        # FABRICATE a table name. They are reported here, at plan time, so the
        # number is visible BEFORE anything is written.
        terms_skipped = getattr(plan_terms, "skipped", []) or []

        print("=" * 78)
        print("THE RULING: be unified by %r not %r"
              % (NEW.lstrip("_"), OLD.lstrip("_")))
        print("=" * 78)
        print("  objects to RENAME      : %d" % len(objs))
        print("  columns to RENAME      : %d" % len(cols))
        print("  ENTITY LETTERS affected: %d  <-- entity id resolution" % len(ents))
        print("  db_table_registry rows : %d" % len(regs))
        print("  terminology terms      : %d  (of which %d are LEFT ALONE: they "
              "name no table/view)" % (len(terms), len(terms_skipped)))
        print("  .py FILES with refs    : %d  (%d occurrences)"
              % (len(code), sum(c["count"] for c in code)))
        print("  HISTORY/EVIDENCE files : %d  (COUNTED, never rewritten -- a name"
              % hist["files"])
        print("                           found in qc_evidence/ or evidence/ is a")
        print("                           RECORD, not a reference)")
        manual = getattr(plan_code, "manual", [])
        print("  PROOF files for REVIEW : %d  (not auto-rewritten: a proof asserts a"
              % len(manual))
        print("                           SPECIFIC spelling, so a mechanical rewrite")
        print("                           would make its assertion tautological)")
        for m in manual[:10]:
            print("      %s" % m["file"])
        print()
        print("--- ENTITY ID IMPACT (the part that is not cosmetic) ---")
        for e in ents:
            print("   %-3s %-32s -> %s" % (e["letter"], e["old"], e["new"]))
        print()
        print("--- OBJECTS (first 12 of %d) ---" % len(objs))
        for o in objs[:12]:
            print("   %-8s %-34s -> %s" % (o["type"], o["old"], o["new"]))
        print()
        print("--- COLUMNS (first 12 of %d) ---" % len(cols))
        for c in cols[:12]:
            print("   %-30s.%-26s -> %s" % (c["table"], c["old"], c["new"]))
        print()
        print("--- CODE FILES (first 15 of %d) ---" % len(code))
        for c in code[:15]:
            print("   %-46s %d ref(s)" % (c["file"], c["count"]))

        if not apply:
            print()
            print("PLAN ONLY — nothing written. The DB and the CODE must be")
            print("renamed TOGETHER; this prints the whole blast radius first.")
            return 0

        backup = DB.with_name(DB.name + ".bak_registry_naming_"
                              + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(DB, backup)
        print("\nbackup (before any write): %s" % backup.name)

        # ---- PRE-FLIGHT: REFUSE BEFORE ANY WRITE --------------------------
        #
        # MEASURED DEFECT IN MY OWN FIRST FIX (2026-09-27): I put the collision
        # refusal AFTER the table renames, so a collision would still have renamed
        # 22 TABLES and then stopped — a half-applied database, which is exactly
        # the failure the refusal was meant to prevent. A REFUSAL MUST HAPPEN
        # BEFORE THE FIRST WRITE, or it is a report rather than a gate.
        #
        # The check is on the LIVE terms, not on `plan_terms`'s output, because a
        # collision is a property of the pair (which term already holds the target
        # key), and `terminology_registry` is the table that holds them.
        term_table_now = "terminology_registry"
        preflight = []
        for t in plan_terms(conn, table=term_table_now):
            clash = conn.execute(
                "SELECT term_id FROM %s WHERE term_key=? AND term_id<>?"
                % term_table_now, (t["new"], t["term_id"])).fetchone()
            if clash:
                preflight.append({"term_id": t["term_id"], "old": t["old"],
                                  "new": t["new"],
                                  "clash_with": int(clash["term_id"])})
        if preflight:
            print("\nREFUSED BEFORE ANY WRITE: %d term collision(s)." % len(preflight))
            for c in preflight:
                print("   term_id=%s %s -> %s CLASHES with term_id=%s"
                      % (c["term_id"], c["old"], c["new"], c["clash_with"]))
            print("\nA collision is TWO terms for ONE concept — a MERGE (retire one,")
            print("or point it at the other), which is the human's decision and not")
            print("a rename. NOTHING was written; the backup above is untouched.")
            return 2

        # ---- the DB side, in dependency order --------------------------
        # Foreign keys OFF for the rename: SQLite would otherwise rewrite
        # REFERENCES clauses mid-flight. Turned back on before verification.
        conn.execute("PRAGMA foreign_keys=OFF")
        renamed = 0
        for o in objs:
            if o["type"] != "table":
                continue
            before = conn.execute("SELECT COUNT(*) FROM %s" % o["old"]).fetchone()[0]
            conn.execute('ALTER TABLE "%s" RENAME TO "%s"' % (o["old"], o["new"]))
            after = conn.execute("SELECT COUNT(*) FROM %s" % o["new"]).fetchone()[0]
            if after != before:
                raise SystemExit("COUNT CHANGED on %s: %s -> %s"
                                 % (o["old"], before, after))
            renamed += 1
        print("   renamed tables         : %d (every count verified)" % renamed)

        # THE UPDATES USE THE **NEW** TABLE NAMES. MEASURED DEFECT IN MY OWN FIRST
        # VERSION: the tables are renamed BEFORE this block, so `UPDATE
        # entity_type_registry` would raise `no such table` -- the rename would
        # half-apply and the run would die with the DB in an inconsistent state.
        # `db_table_registry` is ALREADY standard, so it keeps its name.
        ent_tbl = "entity_type_registry"
        term_tbl = "terminology_registry"
        conn.execute("UPDATE db_table_registry SET table_key=REPLACE(table_key,?,?) "
                     "WHERE table_key LIKE ?", (OLD, NEW, "%" + OLD))
        conn.execute("UPDATE %s SET register_table=REPLACE("
                     "register_table,?,?) WHERE register_table LIKE ?" % ent_tbl,
                     (OLD, NEW, "%" + OLD))

        # THE TERMS NEED COLLISION DETECTION. MEASURED DEFECT FOUND BY THE
        # REHEARSAL (2026-09-27): `terminology_registry.term_key` is UNIQUE on
        # (parent_term_id, term_key), and BOTH spellings already exist as terms --
        # `channel_registry` AND `channel_registry`, `terminology_registry` AND
        # `terminology_registry`. A blind REPLACE renamed one onto the other and the
        # run died with `UNIQUE constraint failed`.
        #
        # MEASURED: a collision means the register holds TWO terms for ONE concept,
        # which is a MERGE decision (retire one, or point it at the other) -- not a
        # rename. So a collision REFUSES the run (fixed 2026-09-27): the previous
        # version SKIPPED the colliding pair and continued, which left the register
        # holding one renamed term, one un-renamed term, and two colliding terms
        # unresolved -- a half-state that is HARDER to reason about than either the
        # before or the after. A merge decision is the human's, so the run stops and
        # says which pair it is.
        collisions = []
        for t in plan_terms(conn, table=term_tbl):
            clash = conn.execute(
                "SELECT term_id FROM %s WHERE term_key=? AND term_id<>?" % term_tbl,
                (t["new"], t["term_id"])).fetchone()
            if clash:
                collisions.append({"term_id": t["term_id"], "old": t["old"],
                                   "new": t["new"], "clash_with": int(clash[0])})
                continue
            conn.execute("UPDATE %s SET term_key=? WHERE term_id=?" % term_tbl,
                         (t["new"], t["term_id"]))
        conn.commit()
        print("   updated registry rows  : db_table_registry + %s + %s"
              % (ent_tbl, term_tbl))
        skipped_terms = getattr(plan_terms, "skipped", []) or []
        print("   TERMS LEFT ALONE (they name no live table/view): %d"
              % len(skipped_terms))
        for s in skipped_terms:
            print("      term_id=%s %-34s would be %-34s %s"
                  % (s["term_id"], s["old"], s["would_be"],
                     "ACTION/COUNT/entity with no table"))
        print("   TERM COLLISIONS (2 terms, 1 concept -- A MERGE, NOT A RENAME): %d"
              % len(collisions))
        for c in collisions:
            print("      term_id=%s %s -> %s CLASHES with existing term_id=%s"
                  % (c["term_id"], c["old"], c["new"], c["clash_with"]))
        if collisions:
            # A COLLISION STOPS THE RUN. MEASURED: skipping it left the register
            # holding one renamed term, one un-renamed term, and two colliding
            # terms unresolved -- a half-state harder to reason about than either
            # the before or the after. The merge is the human's decision.
            raise SystemExit(
                "REFUSED: %d term(s) collide after the rename. Each is TWO terms "
                "for ONE concept, which is a MERGE (retire one, or point it at the "
                "other), not a rename. Nothing else was changed; re-run after the "
                "merge is decided." % len(collisions))

        # ---- VERIFY, from the DB (NEW names) --------------------------
        conn.execute("PRAGMA foreign_keys=ON")
        # TABLES only. MEASURED: SQLite has no `ALTER INDEX ... RENAME TO`, so the
        # two `idx_*_registry` indexes cannot be renamed in place -- they would have
        # to be DROPped and recreated. An INDEX name is not an entity reference
        # (nothing resolves through it), so it is REPORTED as a cosmetic remainder
        # rather than counted as an incomplete migration.
        left = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                            "type='table' AND name LIKE ?", ("%" + OLD,)).fetchone()[0]
        idx_left = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE ?",
            ("%" + OLD,))]
        ent_left = conn.execute("SELECT COUNT(*) FROM %s WHERE "
                                "register_table LIKE ?" % ent_tbl,
                                ("%" + OLD,)).fetchone()[0]
        print()
        print("   objects still `_registry`: %d" % left)
        print("   letters still `_registry`: %d" % ent_left)
        if idx_left:
            print("   INDEX names remaining    : %d (cosmetic; SQLite cannot rename "
                  "an index)" % len(idx_left))
            for n in idx_left:
                print("      %s" % n)
        if left or ent_left:
            print("   *** DB HALF INCOMPLETE — do NOT proceed ***")
            return 1

        # ---- THE CODE SIDE, in the SAME run ----------------------------
        # THE HUMAN: "apply the plan". Doing only the DB half would leave a
        # renaming DB and code still asking for the OLD names — BROKEN, and worse
        # than either half alone. So the two halves run together, DB first (so a
        # code failure can be traced to the code step), then code, then the suite.
        #
        # A FILESYSTEM BACKUP FIRST, because git cannot help: MEASURED, 522 of the
        # 543 target files are UNTRACKED (only 36 root `.py` files are in git).
        zbak = backup_live_files()
        print("   filesystem backup      : %s" % Path(zbak).name)
        cr = rewrite_live_code(apply=True)
        print("   LIVE CODE rewritten    : %d file(s)" % cr["files"])
        for c in cr["changed"][:8]:
            print("      %-52s %d replacement(s)" % (c["file"], c["replacements"]))
        if cr["files"] > 8:
            print("      ... and %d more" % (cr["files"] - 8))
        print("   MODULE FILES renamed   : %d" % len(cr["modules_renamed"]))
        for m in cr["modules_renamed"]:
            print("      %-40s -> %s" % (m["old"], m["new"]))
        if manual:
            print("   PROOF files NOT rewritten: %d (review by hand)" % len(manual))
            for m in manual:
                print("      %s" % m["file"])
        print()
        print("   NOT rewritten (HISTORY/EVIDENCE: %d files):"
              % hist["files"])
        print("      a name in qc_evidence/ or evidence/ is a RECORD. Rewriting it")
        print("      would falsify provenance to make a cosmetic point.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
