# -*- coding: utf-8 -*-
"""
migrate_legacy_ids.py — retire the legacy capability numbering, in ONE place.

The problem, measured
---------------------
One capability set was numbered THREE different ways at once:

    1. `ontology_store.seed_ontology_registry_defaults()` inserts
       `CP-S-00` .. `CP-S-08` into `capability_registry` (9 real rows)
    2. `skill_task_validate.CAPABILITY_MODULE` hard-codes the same `CP-S-*`
       keys as a static snapshot to validate against
    3. `skill_prompt_ext.SKILL_CAPABILITIES` assigns the same keys to workers

and a FOURTH reference (`CP-S-09`) exists in `skill_prompt_ext.py` with NO
matching registry row -- a reference pointing at nothing.

Three copies of one fact is what let one capability appear under several names.
The fix is not to edit three files in step; it is to have ONE semantic key and
let every reader look it up.

What this module deliberately does NOT touch
--------------------------------------------
`catalog_id` / `subcatalog_id` also matched a search for "legacy ids". They are
NOT legacy ids: they form the chat-message classification tree, with real rows
and foreign keys. Renaming them would break working features to tidy a name.
They are out of scope, and `out_of_scope()` says so explicitly so that a later
reader does not "finish the job" by accident.

Old ids stay QUERYABLE
----------------------
The old numbering is retired, but it is recorded in `legacy_id_map`
(append-only). A migration that leaves no trail cannot be audited or reversed.
The map is a record, not a second SSOT.

Dry-run by default; nothing writes unless `--apply`.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

LEGACY_ID_RE = re.compile(r"\bCP-S-\d{2}\b")

# Legacy key -> the semantic key that replaces it. The right-hand side is
# `{module}.{capability}`, matching the convention the registry already uses for
# its non-legacy rows (`task_center.validate_new_task`, `capability.ssot`).
LEGACY_CAPABILITY_MAP: dict[str, str] = {
    "CP-S-00": "mouse_spot_helper.core",
    "CP-S-01": "mouse_spot_helper.prompt_load",
    "CP-S-02": "mouse_spot_helper.prompt_render",
    "CP-S-03": "mouse_spot_helper.mouse_spot_verify",
    "CP-S-04": "mouse_spot_helper.prompt_regression",
    "CP-S-05": "mouse_spot_helper.prompt_promote",
    "CP-S-06": "mouse_spot_helper.skills_api",
    "CP-S-07": "mouse_spot_helper.prompt_ssot",
    "CP-S-08": "mouse_spot_helper.events",
    # Referenced by skill_prompt_ext.py but NEVER seeded into the registry.
    # Included so the dangling reference can be repaired to a real key.
    "CP-S-09": "mouse_spot_helper.task_format_validator",
}

LEGACY_MAP_DDL = """
CREATE TABLE IF NOT EXISTS legacy_id_map (
    map_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT    NOT NULL,
    old_id      TEXT    NOT NULL,
    new_id      TEXT    NOT NULL,
    seen_in     TEXT,
    note        TEXT,
    migrated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, old_id)
);
"""

# Files that reference the legacy ids. A migration that renames the DB but
# leaves the references would produce a validator whose static snapshot
# disagrees with the registry -- the same class of defect being fixed.
SCAN_SKIP_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "dist", "out",
    "chrome_cdp_profile", ".vite", ".pytest_cache", "site-packages",
})
SCAN_SKIP_FILES = frozenset({
    "migrate_legacy_ids.py",
    "_proof_legacy_id_map.py",
    # ADDED 2026-09-22. `_proof_c3_legacy_retire.py` is a TEST: it deliberately
    # writes a legacy key to prove the C3 check goes RED when one is active, and
    # it holds the retired->replacement PAIRS as its own fixture. Those are test
    # DATA, not dangling references — the same reason the two files above are
    # skipped. Without this the report cried wolf about the very proof that
    # verifies the retirement.
    "_proof_c3_legacy_retire.py",
    # ADDED 2026-09-22. `_retire_legacy_capabilities.py` is the RETIREMENT TOOL:
    # it must NAME the keys it retires, or it could not retire them.
    "_retire_legacy_capabilities.py",
    # ADDED 2026-09-22. `_qc_capability_tag.py` is a QC REPORT: it QUOTES the
    # failing message ("9 legacy key(s) still registered") as its evidence. A
    # report that could not quote the defect would not be a report.
    "_qc_capability_tag.py",
    # ADDED 2026-09-22. `_submit_c3_case_to_chat.py` is the CASE SUBMISSION: it
    # states the finding ("9 rows carried legacy numeric keys CP-S-00..CP-S-08")
    # as the evidence for the case. Same reason as the QC report above.
    "_submit_c3_case_to_chat.py",
})

OUT_OF_SCOPE: tuple[tuple[str, str], ...] = (
    ("catalog_id", "chat-message classification tree (real rows + FK)"),
    ("subcatalog_id", "chat-message classification tree (real FK)"),
    ("W-S-*-*", "worker ids; a different document, not a capability key"),
)


def log(msg: str) -> None:
    print("[migrate_legacy_ids] %s" % msg, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_map_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LEGACY_MAP_DDL)
    conn.commit()


def out_of_scope() -> dict[str, str]:
    """What this migration refuses to touch, and why. Named, not implied."""
    return {p: why for p, why in OUT_OF_SCOPE}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def find_references(root: Path | None = None) -> list[dict[str, Any]]:
    """Every source line that still names a legacy id."""
    out: list[dict[str, Any]] = []
    for p in sorted(Path(root or BASE_DIR).rglob("*.py")):
        if any(part in SCAN_SKIP_DIRS for part in p.parts):
            continue
        if p.name in SCAN_SKIP_FILES:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for m in LEGACY_ID_RE.finditer(line):
                try:
                    rel = p.relative_to(BASE_DIR).as_posix()
                except Exception:
                    rel = p.as_posix()
                stripped = line.strip()
                # A comment that NAMES a retired id is the migration trail, not
                # a dangling reference. Conflating them would make the report
                # cry wolf about the very notes that record the rename.
                is_comment = stripped.startswith("#")
                if not is_comment:
                    # an inline `#` after code still makes the match a comment
                    before = line[:m.start()]
                    if "#" in before:
                        is_comment = True
                out.append({"file": rel, "line": i, "id": m.group(0),
                            "cite_ref": "%s:%d" % (rel, i),
                            "is_comment": is_comment,
                            "text": stripped[:110]})
    return out


def plan(conn: sqlite3.Connection, *, root: Path | None = None) -> dict[str, Any]:
    """Everything the migration would change, and every reference it would leave."""
    ensure_map_schema(conn)

    reg: dict[str, dict] = {}
    if _table_exists(conn, "capability_registry"):
        for r in conn.execute("SELECT capability_id, capability_key, module_id "
                              "FROM capability_registry"):
            reg[r["capability_key"]] = dict(r)

    renames: list[dict] = []
    for old, new in sorted(LEGACY_CAPABILITY_MAP.items()):
        if old not in reg:
            renames.append({"old": old, "new": new, "state": "absorbs_only",
                            "why": "no registry row; the reference must be "
                                   "repaired so it stops pointing at nothing"})
        elif new in reg:
            renames.append({"old": old, "new": new, "state": "collision",
                            "why": "target %r already exists; reconcile, do not "
                                   "overwrite" % new})
        else:
            renames.append({"old": old, "new": new, "state": "rename",
                            "capability_id": reg[old]["capability_id"]})

    refs = find_references(root)
    known = set(LEGACY_CAPABILITY_MAP)
    live = [f for f in refs if not f.get("is_comment")]
    comments = [f for f in refs if f.get("is_comment")]
    unresolved = sorted({f["id"] for f in live if f["id"] not in known})

    return {
        "ok": True,
        "map_size": len(LEGACY_CAPABILITY_MAP),
        "registry_rows": len(reg),
        "renames": renames,
        "rename_count": sum(1 for r in renames if r["state"] == "rename"),
        "collisions": [r for r in renames if r["state"] == "collision"],
        "absorbs_only": [r for r in renames if r["state"] == "absorbs_only"],
        "reference_files": refs,
        "live_reference_files": live,
        "comment_reference_files": comments,
        "live_reference_count": len(live),
        "comment_reference_count": len(comments),
        "reference_file_count": len({f["file"] for f in refs}),
        "unresolved_legacy_ids": unresolved,
        # A dangling reference is one in CODE. A comment naming a retired id is
        # the trail that explains the rename and is expected to remain.
        "dangling_references": sorted({f["id"] for f in live
                                       if f["id"] not in reg}),
        "out_of_scope": out_of_scope(),
        "map_covers_all_seen": not unresolved,
        "code_is_clean": not live,
    }


def apply(conn: sqlite3.Connection, *, dry_run: bool = True) -> dict[str, Any]:
    """Rename the registry rows and record the mapping. Never deletes history."""
    ensure_map_schema(conn)
    p = plan(conn)
    done: list[dict] = []
    skipped: list[dict] = []
    for r in p["renames"]:
        if r["state"] != "rename":
            skipped.append(r)
            continue
        if dry_run:
            done.append({**r, "dry_run": True})
            continue
        conn.execute(
            "UPDATE capability_registry SET capability_key = ?, updated_at = ? "
            "WHERE capability_key = ?", (r["new"], _utc_now(), r["old"]))
        conn.execute(
            "INSERT OR REPLACE INTO legacy_id_map "
            "(entity_type, old_id, new_id, seen_in, note, migrated_at) "
            "VALUES ('capability', ?, ?, ?, ?, ?)",
            (r["old"], r["new"], "capability_registry",
             "renamed by migrate_legacy_ids", _utc_now()))
        done.append(r)
    if not dry_run:
        conn.commit()
    return {"ok": True, "dry_run": dry_run, "renamed": len(done), "rows": done,
            "skipped": skipped,
            "live_reference_count": p["live_reference_count"],
            "comment_reference_count": p["comment_reference_count"],
            "code_is_clean": p["code_is_clean"],
            "unresolved_legacy_ids": p["unresolved_legacy_ids"]}


def lookup_old(conn: sqlite3.Connection, old_id: str) -> dict | None:
    """Read back a retired id. This is why the map exists."""
    ensure_map_schema(conn)
    row = conn.execute("SELECT * FROM legacy_id_map WHERE old_id = ?",
                       (old_id,)).fetchone()
    return dict(row) if row else None


# Legacy ids that were REFERENCED in code but had NO registry row. Renaming them
# is not enough: the reference still points at nothing until a real row exists.
# (module_key, capability_suffix) for each.
DANGLING_TO_CREATE: dict[str, tuple[str, str]] = {
    "CP-S-09": ("mouse_spot_helper", "task_format_validator"),
}


def repair_dangling(conn: sqlite3.Connection, *,
                    dry_run: bool = True) -> dict[str, Any]:
    """Create the registry row a dangling reference needs, parented for real.

    The parent module is resolved from `module_registry`; if it is absent the row
    is NOT created, because a capability with an invented parent would make the
    FK graph lie.
    """
    if not _table_exists(conn, "capability_registry"):
        return {"ok": False, "why": "capability_registry absent"}
    existing = {r["capability_key"] for r in conn.execute(
        "SELECT capability_key FROM capability_registry")}
    created: list[dict] = []
    skipped: list[dict] = []
    for old, (module_key, suffix) in sorted(DANGLING_TO_CREATE.items()):
        new = "%s.%s" % (module_key, suffix)
        if new in existing:
            skipped.append({"old": old, "new": new, "why": "already a real row"})
            continue
        mod = None
        if _table_exists(conn, "module_registry"):
            mod = conn.execute(
                "SELECT module_id FROM module_registry WHERE module_key = ? "
                "AND is_active = 1", (module_key,)).fetchone()
        if not mod:
            skipped.append({"old": old, "new": new,
                            "why": "module %r not registered; refusing to invent "
                                   "a parent" % module_key})
            continue
        if dry_run:
            created.append({"old": old, "new": new,
                            "module_id": mod["module_id"], "dry_run": True})
            continue
        conn.execute(
            "INSERT INTO capability_registry (capability_key, name, module_id, "
            "is_active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (new, "Mouse Spot %s" % suffix.replace("_", " "),
             mod["module_id"], _utc_now(), _utc_now()))
        conn.execute(
            "INSERT OR REPLACE INTO legacy_id_map "
            "(entity_type, old_id, new_id, seen_in, note, migrated_at) "
            "VALUES ('capability', ?, ?, ?, ?, ?)",
            (old, new, "skill_prompt_ext.py",
             "dangling reference repaired: a real registry row now exists",
             _utc_now()))
        created.append({"old": old, "new": new, "module_id": mod["module_id"]})
    if not dry_run:
        conn.commit()
    return {"ok": True, "dry_run": dry_run, "created": len(created),
            "rows": created, "skipped": skipped}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="really rename (default is a dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        p = plan(conn)
        if args.json:
            print(json.dumps(p, indent=2, ensure_ascii=False, default=str))
        else:
            print("capability rename plan: %d rename | %d collision | %d absorb-only"
                  % (p["rename_count"], len(p["collisions"]),
                     len(p["absorbs_only"])))
            for r in p["renames"]:
                print("   %-8s -> %-42s %s" % (r["old"], r["new"], r["state"]))
            print("\nsource references still present: %d in CODE, %d in comments"
                  % (p["live_reference_count"], p["comment_reference_count"]))
            print("   code is clean: %s" % p["code_is_clean"])
            per: dict[str, int] = {}
            for f in p["live_reference_files"]:
                per["%s (CODE)" % f["file"]] = per.get(
                    "%s (CODE)" % f["file"], 0) + 1
            for f in p["comment_reference_files"]:
                per["%s (comment)" % f["file"]] = per.get(
                    "%s (comment)" % f["file"], 0) + 1
            for f, n in sorted(per.items()):
                print("   %-56s %d" % (f, n))
            if p["dangling_references"]:
                print("\nDANGLING IN CODE (referenced but no registry row): %s"
                      % ", ".join(p["dangling_references"]))
            print("\nOUT OF SCOPE (deliberately untouched):")
            for k, v in p["out_of_scope"].items():
                print("   %-14s %s" % (k, v))
        if args.apply:
            res = apply(conn, dry_run=False)
            print("\nAPPLIED: renamed=%d skipped=%d"
                  % (res["renamed"], len(res["skipped"])))
            rep = repair_dangling(conn, dry_run=False)
            print("dangling repaired: created=%d skipped=%d"
                  % (rep["created"], len(rep["skipped"])))
            for s in rep["skipped"]:
                print("   skipped %s -> %s (%s)" % (s["old"], s["new"], s["why"]))
        else:
            rep = repair_dangling(conn, dry_run=True)
            if rep.get("created") or rep.get("skipped"):
                print("\ndangling repair plan: %d to create, %d skipped"
                      % (rep["created"], len(rep["skipped"])))
                for r in rep["rows"]:
                    print("   %-8s -> %-42s module_id=%s"
                          % (r["old"], r["new"], r.get("module_id")))
                for s in rep["skipped"]:
                    print("   skipped %s -> %s (%s)" % (s["old"], s["new"], s["why"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()