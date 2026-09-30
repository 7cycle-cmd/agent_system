# -*- coding: utf-8 -*-
"""entity_backfill.py — give every SOURCE FILE an entity id and a location.

WHY (the user, 2026-09-23)
--------------------------
    "i know, problem is write need to have entity id"
    "so all under the rule"

THE ORDER IS FORCED, NOT CHOSEN
-------------------------------
`code_location_registry` maps `(entity_type, entity_ref_id, version)` to
`file_path` + `line_start`. The entity-id write gate READS it. MEASURED (P0,
`_diag_registry_fill_state.py`):

    code_location_registry     0 rows
    entity_type_registry      20 rows
    version_registry         240 rows

So the gate's prerequisite is EMPTY. If the gate is switched on first it denies
EVERY write — including the writes that would fill it. Backfill FIRST.

WHAT IT DELEGATES (it re-implements NONE of them)
-------------------------------------------------
    entity_registry.mint_entity()           the id itself (fail-closed)
    code_location_registry.add_location()   the file<->entity mapping

`entity_registry.register_row()` WAS REMOVED 2026-09-27, with
`db_row_registry`. MEASURED: that register was 100% a duplicate of
`code_registry.file_path`, added 0 unique rows against `code_location_registry`,
and made 0 of 300 entity ids verify. The id is now
`{LETTER}-{table_id}-{row_id}-{version}` (`entity_id.SHAPE`). The 3-part form
`{LETTER}-{ref_id}-{version}` is the OLD one (2026-09-27).

THE LETTER IS DATA, NOT A LITERAL
---------------------------------
`R` = code, resolved from `entity_type_registry` (measured: `R` -> `code_registry`,
pk `id`). The letter is READ, never typed into a branch.

WHY A FILE-LEVEL `code_registry` ROW
------------------------------------
`mint_entity()` needs a `ref_id` that resolves against the letter's register, and
`code_registry` is that register for `R`. A source FILE is not a function, so the
row is written with `function_name = '<file>'` and a `notes` line saying so — the
row is HONEST about being file-level rather than pretending to be a function.

`module_name` is the repo-relative path WITHOUT the extension, so two files with
the same stem in different directories cannot collide on
`UNIQUE (module_name, function_name)`.

THE CITATION IS CHECKABLE
-------------------------
`add_location()` REFUSES an empty `cite_ref` ("no citation, no location"). The
citation here is `<relpath>:1` — the file's own first line, which
`terminology_cite.verify_cite_ref()` accepts because the file exists and line 1
is in range.

Run:
    .\\.venv\\Scripts\\python.exe entity_backfill.py --measure
    .\\.venv\\Scripts\\python.exe entity_backfill.py --apply
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import code_location_registry as clr  # noqa: E402
import entity_registry as er  # noqa: E402

DB = BASE / "agent.db"

# Directories that hold no PRODUCT code. The same convention `hardcode_scan`
# uses (`SKIP_DIRS`), so the two agree about what "the repo's code" means.
SKIP_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "dist", "out",
    "chrome_cdp_profile", ".vite", ".pytest_cache", "site-packages",
    "hb_snapshots", "debug_shots", "helper_watchdog_snaps", "evidence",
    "evidence_final", "evidence_steps", "fault_evidence", "qc_evidence",
    "hko_proof", "skills", "docs",
})

# A leading `_` marks a one-shot SESSION script, not product code — the repo
# convention `hardcode_scan.SESSION_SCRIPT_PREFIX` records. A session script is
# not a thing the gate should have to cover.
SESSION_PREFIX = "_"


def log(msg: str) -> None:
    print("[entity_backfill] %s" % msg, flush=True)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def code_letter(conn: sqlite3.Connection) -> dict[str, Any]:
    """The letter that names CODE, read from `entity_type_registry`.

    NOT a literal. If the register says a different letter names code, this
    follows it — a hard-coded `R` would silently mint ids of the wrong kind the
    day the register changes.
    """
    row = conn.execute(
        "SELECT type_letter, entity_kind, register_table, pk_column "
        "  FROM entity_type_registry WHERE entity_kind = 'code' "
        " ORDER BY type_letter LIMIT 1").fetchone()
    if not row:
        return {"ok": False,
                "why": "no entity_type_registry row with entity_kind='code'"}
    return {"ok": True, "letter": row["type_letter"],
            "register_table": row["register_table"],
            "pk_column": row["pk_column"]}


def source_files(base: Path | None = None) -> list[Path]:
    """Every PRODUCT source file, sorted. Deterministic, so two runs agree."""
    b = base or BASE
    out: list[Path] = []
    for p in sorted(b.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith(SESSION_PREFIX):
            continue
        out.append(p)
    return out


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except Exception:
        return p.as_posix()


def _module_key(rel: str) -> str:
    """The repo-relative path without the extension — unique per FILE.

    WHY NOT the stem: `src/task_center/x.py` and `x.py` share a stem, and
    `code_registry` is `UNIQUE (module_name, function_name)`, so a stem would
    make the second file collide with the first and be silently skipped.
    """
    return rel[:-3] if rel.endswith(".py") else rel


def _ensure_code_row(conn: sqlite3.Connection, rel: str, *,
                     apply: bool) -> dict[str, Any]:
    """The `code_registry` row a file-level entity resolves against.

    Idempotent on `register_id`. Returns `{ok, id, created}`.
    """
    mod = _module_key(rel)
    reg_id = "reg_file_%s" % mod.replace("/", "_").replace("\\", "_")
    row = conn.execute(
        "SELECT id FROM code_registry WHERE register_id = ?", (reg_id,)).fetchone()
    if row:
        return {"ok": True, "id": int(row["id"]), "created": False}
    if not apply:
        return {"ok": True, "id": None, "created": True, "dry_run": True}
    cur = conn.execute(
        "INSERT INTO code_registry (register_id, module_name, function_name, "
        "  file_path, line_start, status, source, notes) "
        "VALUES (?, ?, ?, ?, 1, 'active', 'entity_backfill', ?)",
        (reg_id, mod, "<file>", rel,
         "FILE-LEVEL row: this entity names the FILE %s, not a function in it. "
         "Written by entity_backfill.py so the entity-id write gate can cover "
         "the file." % rel))
    conn.commit()
    return {"ok": True, "id": int(cur.lastrowid), "created": True}


def backfill(conn: sqlite3.Connection, *, apply: bool = False,
             base: Path | None = None,
             limit: int | None = None) -> dict[str, Any]:
    """Mint one entity per source file and record its location.

    `apply=False` is a DRY RUN: it resolves everything and writes NOTHING, so the
    plan can be inspected before the register is touched.
    """
    b = base or BASE
    cl = code_letter(conn)
    if not cl.get("ok"):
        return {"ok": False, "error_code": "NO_CODE_LETTER", "error": cl["why"]}
    letter = cl["letter"]

    # The row's owning table must be registered, or `mint_entity` refuses at
    # step 4 ("no row of it can be named"). Checked ONCE, up front, so the
    # failure is a named error instead of 252 identical refusals.
    owner = er.register_table_id(conn, letter)
    if owner is None:
        return {"ok": False, "error_code": "REGISTER_TABLE_NOT_REGISTERED",
                "error": ("%s's register table %s is not in db_table_registry, "
                          "so no row of it can be named"
                          % (letter, cl["register_table"]))}

    files = source_files(b)
    total = len(files)
    picked = files[:limit] if limit else files
    truncated = total > len(picked)

    minted: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for p in picked:
        rel = _rel(p, b)
        cite = "%s:1" % rel
        row = _ensure_code_row(conn, rel, apply=apply)
        if not row.get("ok"):
            refused.append({"file": rel, "code": "CODE_ROW_FAILED",
                            "why": row.get("why")})
            continue
        if not apply:
            minted.append({"file": rel, "cite_ref": cite, "dry_run": True,
                           "code_registry_id": row.get("id")})
            continue
        # 1. the id itself. THE ROW PART WAS REMOVED 2026-09-27, with
        # `db_row_registry` (100% a duplicate of `code_registry.file_path`,
        # 0 unique rows against `code_location_registry`, 0/300 ids verifying).
        me = er.mint_entity(conn, letter, int(row["id"]),
                            version=1, note="entity_backfill: %s" % rel,
                            created_by="entity_backfill")
        if not me.get("ok"):
            refused.append({"file": rel, "code": "MINT_REFUSED",
                            "why": me.get("why")})
            continue
        # 3. the file<->entity mapping the GATE reads
        loc = clr.add_location(conn, letter, int(row["id"]), 1, rel,
                               line_start=1, line_end=1, code_span="file",
                               cite_ref=cite)
        if not loc.get("ok"):
            refused.append({"file": rel, "code": "LOCATION_REFUSED",
                            "why": loc.get("code")})
            continue
        minted.append({"file": rel, "entity_id": me["entity_id"],
                       "cite_ref": cite, "location_id": loc.get("location_id"),
                       "created": loc.get("created")})

    return {
        "ok": True, "letter": letter, "register_table": cl["register_table"],
        "owner_db_table_id": owner,
        "total": total, "returned": len(picked), "truncated": truncated,
        "applied": apply,
        "minted": len(minted), "refused": len(refused),
        "rows": minted, "refused_rows": refused,
    }


def normalize_path(file_path: str | Path, base: Path | None = None) -> str:
    """A repo-relative posix path, or '' when it is outside the repo.

    WHY THIS EXISTS: the gate receives an ABSOLUTE path from VS Code
    (`c:\\projects\\agent_system\\scripts\\plan_gate.py`) while
    `code_location_registry.file_path` holds a RELATIVE one
    (`scripts/plan_gate.py`). Comparing them raw would report "not covered" for
    every file — a gate that denies everything, which is the failure this whole
    task exists to remove.
    """
    b = (base or BASE).resolve()
    try:
        p = Path(file_path)
        if not p.is_absolute():
            return p.as_posix()
        return p.resolve().relative_to(b).as_posix()
    except Exception:
        return ""


def covered_by(conn: sqlite3.Connection, file_path: str | Path, *,
               base: Path | None = None) -> dict[str, Any]:
    """Which entity ids cover this file? THE GATE'S QUESTION, answered once.

    A file is COVERED when `code_location_registry` holds a row whose
    `file_path` is this file. `is_active` is deliberately NOT part of the test:
    `add_location()` writes `is_active=0` by design (activation is
    `activation_gate`'s decision, not the backfill's), so requiring it here would
    make every backfilled row invisible and the gate would deny everything.

    Returns `{ok, file, entities, reason}`. `ok=False` means NOT covered.
    """
    rel = normalize_path(file_path, base)
    if not rel:
        return {"ok": False, "file": "", "entities": [],
                "reason": "path is outside the repo, so no entity can cover it"}
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT entity_type, entity_ref_id, version, cite_ref "
            "  FROM code_location_registry WHERE file_path = ? "
            " ORDER BY entity_type, entity_ref_id, version", (rel,))]
    except sqlite3.OperationalError:
        return {"ok": False, "file": rel, "entities": [],
                "reason": "code_location_registry is absent"}
    spanning: list[dict] = []
    if not rows:
        # ---- SPAN COVERAGE: THE ANSWER TO "A NEW FILE HAS NO ROW" ----------
        #
        # MEASURED 2026-09-29, and it is what lets the allowlist EXCEPTION be
        # deleted. The old rule matched `file_path = rel` EXACTLY, so a file that
        # did not yet exist could never be covered — which is why the plan's
        # allowlist had to authorise new files, and why that allowlist became a
        # **1516-file exception mechanism** (measured: 1729 files authorised by
        # some allowlist, 1516 of them with no entity at all).
        #
        # THE TABLE ALREADY HAS THE MECHANISM. `code_span` declares the SPAN an
        # entity covers, and it is POPULATED with four values:
        #
        #     file 389 | capability 35 | module 4 | channel 1
        #
        # and the non-`file` rows ALREADY name directories (`local_pc/`,
        # `local_pc/openclaw_companion/`). So a spanning row is not an invention:
        # it is a value the vocabulary already carries, and supporting it is
        # READING a declaration that was always there.
        #
        # A DIRECTORY ROW COVERS A FILE INSIDE IT, and the test is on the PATH
        # BOUNDARY (`<dir>/` prefix, via LIKE on `file_path || '%'` where
        # `file_path` itself ends in `/`), NEVER a bare substring: `a/` must not
        # cover `a_extra.py`, and `tt/` must not cover `tt2/x.py`.
        try:
            spanning = [dict(r) for r in conn.execute(
                "SELECT entity_type, entity_ref_id, version, cite_ref, "
                "       code_span, file_path "
                "  FROM code_location_registry "
                " WHERE code_span <> 'file' "
                "   AND file_path LIKE '%/' "
                "   AND ? LIKE file_path || '%' "
                " ORDER BY length(file_path) DESC, entity_type, "
                "          entity_ref_id, version", (rel,))]
        except sqlite3.OperationalError:
            spanning = []
        rows = spanning
    if not rows:
        return {"ok": False, "file": rel, "entities": [],
                "reason": "no code_location_registry row names this file, and no "
                          "spanning (module/capability/channel) row covers its "
                          "directory"}
    # THE ID IS `{LETTER}-{table_id}-{row_id}-{version}` (2026-09-27). THE HUMAN:
    #   "letter - table_id - row_id - version_id"
    #   "`db_row_registry`, that is wrong, don't need that"
    #   "example: Function = F / table_id = 10 = table ABC / row id = 11 =
    #    function_registry / version = 1 / will be F-10-11-1"
    #
    # `table_id` is `db_table_registry.db_table_id` of the letter's register, and
    # `row_id` is that register table's OWN PK. NO second register is consulted.
    ids: list[str] = []
    missing: list[str] = []
    for r in rows:
        letter = str(r["entity_type"])
        tid = er.table_id_of_letter(conn, letter)
        if tid is None:
            missing.append("%s (register table not registered)" % letter)
            continue
        ids.append("%s-%d-%d-%d" % (letter, int(tid), int(r["entity_ref_id"]),
                                    int(r["version"])))
    if not ids:
        return {"ok": False, "file": rel, "entities": [],
                "rows": rows, "row_missing": missing,
                "reason": ("%d location(s) name this file, but no letter's "
                           "register table is registered, so no 4-part id can "
                           "be built" % len(rows))}
    return {"ok": True, "file": rel, "entities": ids, "rows": rows,
            "row_missing": missing,
            "reason": "%d location(s) name this file" % len(rows)}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    """The gate's prerequisite, measured. This is what P2 waits on."""
    out: dict[str, Any] = {}
    for t in ("code_location_registry", "version_registry",
              "entity_type_registry", "code_registry"):
        try:
            out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except Exception as exc:
            out[t] = "MISSING (%s)" % type(exc).__name__
    try:
        out["code_location_active"] = conn.execute(
            "SELECT COUNT(*) FROM code_location_registry WHERE is_active=1"
        ).fetchone()[0]
    except Exception:
        out["code_location_active"] = "n/a"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="entity id backfill for source files")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true",
                    help="report the gate prerequisite counts and exit")
    ap.add_argument("--apply", action="store_true",
                    help="really write (default is a dry run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N files (a volume control)")
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.measure:
            m = measure(conn)
            print("== gate prerequisite ==")
            for k, v in m.items():
                print("  %-24s %s" % (k, v))
            return 0

        res = backfill(conn, apply=args.apply, limit=args.limit)
        if not res.get("ok"):
            print("REFUSED: %s -- %s" % (res.get("error_code"), res.get("error")))
            return 1
        print("letter=%s register=%s owner_db_table_id=%s"
              % (res["letter"], res["register_table"], res["owner_db_table_id"]))
        print("total=%d returned=%d truncated=%s applied=%s"
              % (res["total"], res["returned"], res["truncated"], res["applied"]))
        print("minted=%d refused=%d" % (res["minted"], res["refused"]))
        for r in res["rows"][:5]:
            print("   ok  %-46s %s" % (r["file"], r.get("entity_id") or "(dry)"))
        for r in res["refused_rows"][:8]:
            print("   NO  %-46s %s %s" % (r["file"], r["code"], r.get("why")))
        print()
        print("== after ==")
        for k, v in measure(conn).items():
            print("  %-24s %s" % (k, v))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())