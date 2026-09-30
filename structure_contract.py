# -*- coding: utf-8 -*-
"""structure_contract.py — the DECLARED file structure, DERIVED from the registers.

THE USER (2026-09-24)
---------------------
    "file name too, file location and file stucture!! channel / module /
     capability is folder / api / function under capability folder / table
     stucture is same, so we can have guided stucture to prevent fucking coding/
     data anywhere / this why entity has file name and line number"

THE STRUCTURE, DECLARED
-----------------------
    <channel>/                          e.g. local_pc/
        <module>/                       e.g. task_center/
            <capability>/               e.g. validate_new_task/
                <module>_<cap>.py       e.g. task_center_validate_new_task.py

WHY THE DAMAGE IS MEASURABLE (not an opinion)
---------------------------------------------
    .py files in the REPO ROOT (no folder)      801
    .py files inside ANY folder                  11
    code_location_registry rows with a flat path 253 of 261
    code_location_registry is_active rows         0
    path-ish columns on module/capability/channel NONE
    module_registry rows that are FILE-DERIVED junk 36 of 40

So the structure the user described is NOT the structure on disk, and the
registry RECORDS the damage (a file name was accepted as a module).

PATH IS DERIVED, NEVER HAND-TYPED
---------------------------------
MEASURED: `capability_key` starts with `module_key` on only **16 of 41** rows
(`openclaw.system_exec` sits in `openclaw_companion`; `capability.ssot` too), so
the module segment MUST come from `module_id` -> `module_key`, never from
splitting the capability key on a dot. The capability FOLDER + FILE stem is the
part after the LAST dot, which is safe for both forms.

A caller may REGISTER a path only if it EQUALS the derivation; anything else is
`PATH_MISMATCH` naming the expected path. That is the gate that stops
"coding anywhere" without stopping a legitimate move (which must change the
registers, i.e. be a declared act).

Run:
    .\\.venv\\Scripts\\python.exe structure_contract.py --measure
    .\\.venv\\Scripts\\python.exe structure_contract.py --coverage
    .\\.venv\\Scripts\\python.exe structure_contract.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The THREE structural LEVELS, and the entity type letter each one already has
# in `entity_type_registry` (H=channel, M=module, C=capability). No new letter.
STRUCTURE_LEVELS: tuple[tuple[str, str, str], ...] = (
    ("channel", "H", "channel_registry"),
    ("module", "M", "module_registry"),
    ("capability", "C", "capability_registry"),
)

# The FOUR terms that DECLARE the structure, so the layout is a registered thing
# a worker can read instead of a convention in someone's head.
STRUCTURE_TERMS: tuple[tuple[str, str, str, str], ...] = (
    ("channel_folder", "entity",
     "The TOP directory level of the file structure: one folder per CHANNEL "
     "(`channel_registry.channel_key`).",
     "measured: channel_registry holds 7 rows with a channel_key and NO path "
     "column, so the folder is the only carrier the channel can have"),
    ("module_folder", "entity",
     "The SECOND directory level: one folder per MODULE "
     "(`module_registry.module_key`), inside its channel's folder.",
     "measured: module_registry has 40 rows and NO path column, so the folder "
     "must be derived from module_key"),
    ("capability_folder", "entity",
     "The THIRD directory level: one folder per CAPABILITY, named by the part "
     "of `capability_registry.capability_key` after the LAST dot.",
     "measured: capability_key starts with module_key on only 16 of 41 rows, so "
     "the folder is derived from module_id + the last dot segment"),
    ("capability_file", "entity",
     "The FILE a capability lives in: `<module_key>_<capability_folder>.py`, "
     "inside its capability folder. Registered as the entity's file name.",
     "measured: code_location_registry carries entity -> file_path + line_start "
     "but 253 of 261 rows are FLAT paths and all 261 are is_active=0"),
    # THE FOUR FILE LEVELS UNDER THE CAPABILITY FOLDER (2026-09-27, the human:
    # "channel, module, capability, api, function, table, field ... can we have
    # system or skill to help for folder name, filename and location format
    # management"). api/function hang off capability_id (measured: both
    # registries carry capability_id); table/field hang off db_table_id.
    ("api_file", "entity",
     "The FILE an API lives in: `<module_key>_<capability_folder>_api.py`, "
     "inside the capability folder of its `api_registry.capability_id`.",
     "measured: api_registry has 282 rows (43 active) with capability_id and "
     "NO file column, so the file must be derived"),
    ("function_file", "entity",
     "The FILE a function lives in: the capability file itself, "
     "`<module_key>_<capability_folder>.py`, of its "
     "`function_registry.capability_id`. A function is code INSIDE the "
     "capability file, located by line, not by a second file.",
     "measured: function_registry has 4096 rows (13 active) with "
     "capability_id + file_path, so the file is derived and the line is the "
     "location"),
    ("table_file", "entity",
     "The FILE a db table's DDL lives in: `<module_key>_<capability_folder>_"
     "schema.py`, inside the capability folder of the capability that owns "
     "the table. A table has NO capability_id column, so the capability is "
     "the one whose module owns the schema.",
     "measured: db_table_registry has 204 rows (197 active) with NO "
     "capability_id and NO file column, so the file must be derived"),
    ("field_file", "entity",
     "The FILE a db field lives in: the SAME file as its table "
     "(`<module_key>_<capability_folder>_schema.py`), located by line. A "
     "field is a column INSIDE the table's DDL, not a second file.",
     "measured: db_field_registry has 3438 rows (2183 active) with "
     "db_table_id and NO file column, so the file is the table's file"),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


# --------------------------------------------------------------------------
# THE DERIVATION
# --------------------------------------------------------------------------
def capability_part(capability_key: str) -> str:
    """The folder + file stem of a capability: the part after the LAST dot."""
    return str(capability_key or "").strip().rsplit(".", 1)[-1]


def structure_of(conn: sqlite3.Connection, capability_key: str
                 ) -> dict[str, Any]:
    """The DECLARED path + file of one capability, derived from the registers."""
    key = str(capability_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_CAPABILITY_KEY"}
    row = conn.execute(
        "SELECT c.capability_id, c.capability_key, c.module_id, m.module_key, "
        "m.channel_id, ch.channel_key "
        "FROM capability_registry c "
        "JOIN module_registry m ON m.module_id = c.module_id "
        "LEFT JOIN channel_registry ch ON ch.channel_id = m.channel_id "
        "WHERE c.capability_key = ?", (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_CAPABILITY", "capability_key": key,
                "cite": "measured: capability_registry has no key %r" % key}
    part = capability_part(key)
    mod = str(row["module_key"])
    channel = str(row["channel_key"] or "NA")
    path = "%s/%s/%s/" % (channel, mod, part)
    fname = "%s_%s.py" % (mod, part)
    return {"ok": True, "capability_key": key, "channel_key": channel,
            "module_key": mod, "capability_part": part,
            "capability_id": int(row["capability_id"]),
            "module_id": int(row["module_id"]),
            "channel_id": (int(row["channel_id"])
                           if row["channel_id"] is not None else None),
            "path": path, "file_name": fname, "full_path": path + fname,
            "cite": ("measured: capability_registry.capability_id=%d -> "
                     "module_registry.module_id=%d (%s) -> channel %s"
                     % (int(row["capability_id"]), int(row["module_id"]), mod,
                        channel))}


def check_path(conn: sqlite3.Connection, capability_key: str, path: str
               ) -> dict[str, Any]:
    """Does `path` EQUAL the derivation? REFUSES with `PATH_MISMATCH` otherwise.

    The refusal NAMES the expected path, because a gate that only says "no" is
    the deadlock this repo keeps recording.
    """
    s = structure_of(conn, capability_key)
    if not s["ok"]:
        return s
    want = s["path"]
    got = str(path or "").strip().replace("\\", "/")
    if got.rstrip("/") == want.rstrip("/"):
        return {"ok": True, "path": got, "capability_key": capability_key,
                "cite": s["cite"]}
    return {"ok": False, "code": "PATH_MISMATCH", "capability_key": capability_key,
            "path": got, "expected": want,
            "why": ("the path does not equal the DECLARED structure for this "
                    "capability; a path is DERIVED, never hand-typed"),
            "cite": s["cite"]}


# --------------------------------------------------------------------------
# THE FOUR FILE LEVELS (api / function / table / field)
# --------------------------------------------------------------------------
def _cap_of_ref(conn: sqlite3.Connection, table: str, pk: str,
                ref_id: int) -> dict[str, Any]:
    """Resolve a row's capability_id FK to the capability's derived structure.

    `pk` is the row's OWN primary key (`api_id` / `function_id`); the FK is
    the row's `capability_id` column.
    """
    row = conn.execute(
        "SELECT capability_key FROM %s t JOIN capability_registry c ON "
        "c.capability_id = t.capability_id WHERE t.%s = ?" % (table, pk),
        (ref_id,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_CAPABILITY_REF",
                "table": table, "ref_id": ref_id}
    return structure_of(conn, str(row["capability_key"]))


def api_structure_of(conn: sqlite3.Connection, api_id: int) -> dict[str, Any]:
    """The DECLARED file of one API, derived from its capability_id FK."""
    s = _cap_of_ref(conn, "api_registry", "api_id", int(api_id))
    if not s["ok"]:
        return s
    part = s["capability_part"]
    mod = s["module_key"]
    fname = "%s_%s_api.py" % (mod, part)
    return dict(s, level="api", file_name=fname, full_path=s["path"] + fname)


def function_structure_of(conn: sqlite3.Connection,
                          function_id: int) -> dict[str, Any]:
    """The DECLARED file of one function: the capability file itself.

    A function is code INSIDE the capability file; its location is the LINE,
    not a second file. The derivation therefore returns the capability file
    and says so, so a caller cannot invent a per-function file.
    """
    s = _cap_of_ref(conn, "function_registry", "function_id", int(function_id))
    if not s["ok"]:
        return s
    return dict(s, level="function", file_name=s["file_name"],
                full_path=s["full_path"],
                note="a function lives INSIDE the capability file; the line "
                     "is its location, not a second file")


def table_structure_of(conn: sqlite3.Connection, table_key: str) -> dict[str, Any]:
    """The DECLARED file of one db table's DDL.

    MEASURED: `db_table_registry` has NO capability_id column, so the
    capability is NOT an FK — it is the capability whose module owns the
    schema. The derivation therefore returns the FILE NAME PATTERN
    (`<module>_<cap>_schema.py`) and the set of candidate paths, and a
    location row is valid when its path is ONE of them. Inventing a single
    "the" capability for a table would be a second truth.
    """
    key = str(table_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_TABLE_KEY"}
    row = conn.execute(
        "SELECT db_table_id, table_key FROM db_table_registry WHERE "
        "table_key = ?", (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_TABLE", "table_key": key}
    cands: list[dict[str, Any]] = []
    for r in conn.execute(
            "SELECT c.capability_key FROM capability_registry c "
            "JOIN module_registry m ON m.module_id = c.module_id "
            "WHERE c.is_active=1 ORDER BY c.capability_key"):
        s = structure_of(conn, str(r["capability_key"]))
        if not s["ok"]:
            continue
        fname = "%s_%s_schema.py" % (s["module_key"], s["capability_part"])
        cands.append({"capability_key": s["capability_key"],
                      "path": s["path"], "file_name": fname,
                      "full_path": s["path"] + fname})
    return {"ok": True, "level": "table", "table_key": key,
            "db_table_id": int(row["db_table_id"]),
            "file_name_pattern": "<module_key>_<capability_folder>_schema.py",
            "candidates": cands,
            "cite": "measured: db_table_registry has no capability_id, so the "
                    "file is the pattern over %d active capabilities"
                    % len(cands)}


def field_structure_of(conn: sqlite3.Connection, field_key: str) -> dict[str, Any]:
    """The DECLARED file of one db field: the SAME file as its table.

    A field is a column INSIDE the table's DDL; its location is the LINE.
    """
    key = str(field_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_FIELD_KEY"}
    row = conn.execute(
        "SELECT f.field_key, f.db_table_id, t.table_key FROM "
        "db_field_registry f JOIN db_table_registry t ON "
        "t.db_table_id = f.db_table_id WHERE f.field_key = ?", (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_FIELD", "field_key": key}
    t = table_structure_of(conn, str(row["table_key"]))
    if not t["ok"]:
        return t
    return dict(t, level="field", field_key=key,
                db_field_note="a field lives INSIDE the table's schema file; "
                              "the line is its location, not a second file")


# --------------------------------------------------------------------------
# THE STRUCTURE GATE — the actionable form
# --------------------------------------------------------------------------
def check_file(conn: sqlite3.Connection, file_path: str,
               *, root: Path | None = None) -> dict[str, Any]:
    """Is `file_path` a path the DECLARED structure derives to?

    This is what a write would call. It returns `ALLOWED` with the capability it
    belongs to, or `UNSTRUCTURED` with the declared root NAMED — so the caller
    learns WHERE the file belongs instead of only that it was refused.
    """
    root = root or BASE_DIR
    raw = str(file_path or "").strip()
    if not raw:
        return {"ok": False, "code": "EMPTY_PATH"}
    # Accept an absolute path or a repo-relative one.
    try:
        rel = os.path.relpath(raw, str(root)) if os.path.isabs(raw) else raw
    except ValueError:
        rel = raw
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    expects: list[dict[str, Any]] = []
    for r in conn.execute(
            "SELECT c.capability_key FROM capability_registry c "
            "JOIN module_registry m ON m.module_id = c.module_id "
            "WHERE c.is_active=1 ORDER BY c.capability_key"):
        s = structure_of(conn, str(r["capability_key"]))
        if not s["ok"]:
            continue
        expects.append(s)
        if rel == (s["path"] + s["file_name"]) or rel == s["path"].rstrip("/"):
            return {"ok": True, "code": "STRUCTURED", "file": rel,
                    "capability_key": s["capability_key"],
                    "declared_path": s["path"] + s["file_name"],
                    "cite": s["cite"]}
    return {"ok": False, "code": "UNSTRUCTURED", "file": rel,
            "segments": parts,
            "why": ("this path is not one the DECLARED structure derives to "
                    "(%d capabilities were checked)" % len(expects)),
            "declared_root": ("<channel>/<module>/<capability>/"
                              "<module>_<capability>.py"),
            "examples": ["%s%s" % (s["path"], s["file_name"])
                         for s in expects[:3]],
            "cite": "measured: %d derived capability paths, none matched"
                    % len(expects)}


# --------------------------------------------------------------------------
# COVERAGE + the damage report
# --------------------------------------------------------------------------
def retired_capability_names(conn: sqlite3.Connection) -> dict[str, Any]:
    """Capability rows whose key is a SUPERSEDED name (is_active=0 + renamed).

    MEASURED: 9 `CP-S-0N` rows sit in `capability_registry` with `is_active=0`
    while `legacy_id_map` records them as renamed to `mouse_spot_helper.*`. They
    are NOT a second capability — they are the OLD NAME of an existing one, and
    a structure derived from them would name a folder after a RETIRED name.
    REPORTED, never deleted.
    """
    if not _table_exists(conn, "capability_registry"):
        return {"ok": False, "code": "NO_CAPABILITY_REGISTRY"}
    rows = [dict(r) for r in conn.execute(
        "SELECT capability_id, capability_key, is_active, module_id "
        "FROM capability_registry ORDER BY capability_id")]
    old_ids: dict[str, str] = {}
    if _table_exists(conn, "legacy_id_map"):
        old_ids = {str(r["old_id"]): str(r["new_id"])
                   for r in conn.execute("SELECT old_id, new_id FROM "
                                         "legacy_id_map")}
    retired = [dict(r, renamed_to=old_ids.get(str(r["capability_key"])))
               for r in rows if int(r["is_active"]) == 0]
    active = [r for r in rows if int(r["is_active"]) != 0]
    return {"ok": True, "total": len(rows), "active": len(active),
            "retired_count": len(retired),
            "retired_with_a_new_name": sum(1 for r in retired
                                           if r.get("renamed_to")),
            "retired": retired,
            "cite": ("measured: capability_registry.is_active=0 on %d of %d "
                     "rows; legacy_id_map names the replacement for %d"
                     % (len(retired), len(rows),
                        sum(1 for r in retired if r.get("renamed_to"))))}


def path_coverage(conn: sqlite3.Connection, *, root: Path | None = None,
                  active_only: bool = True) -> dict[str, Any]:
    """How many capabilities' files sit where the structure says.

    `active_only=True` by DEFAULT, and that is a correction: an INACTIVE row is a
    RETIRED name, and deriving a folder from it would name a folder after a name
    the system retired.
    """
    root = root or BASE_DIR
    where = "WHERE is_active=1" if active_only else ""
    rows: list[dict[str, Any]] = []
    for r in conn.execute("SELECT capability_key FROM capability_registry %s "
                          "ORDER BY capability_key" % where):
        s = structure_of(conn, str(r["capability_key"]))
        if not s["ok"]:
            rows.append({"capability_key": str(r["capability_key"]),
                         "ok": False, "code": s["code"]})
            continue
        full = root / s["path"] / s["file_name"]
        rows.append({"capability_key": s["capability_key"], "ok": True,
                     "expected": ("%s%s" % (s["path"], s["file_name"])),
                     "exists": full.exists(),
                     "path_shape_ok": bool(str(s["path"]).count("/") == 3)})
    ok = [r for r in rows if r.get("ok")]
    in_place = [r for r in ok if r.get("exists")]
    return {"ok": True, "active_only": bool(active_only),
            "capabilities": len(rows), "derivable": len(ok),
            "file_exists_at_declared_path": len(in_place),
            "not_yet_moved": len(ok) - len(in_place),
            "rows": rows,
            "cite": "measured: structure_of() per capability + a real os check"}


def junk_modules(conn: sqlite3.Connection) -> dict[str, Any]:
    """The FILE-DERIVED module rows. REPORTED, never silently deleted.

    MEASURED: 36 of 40 `module_registry` rows are named after a FILE
    (`_apply_cap_rename`, `_crop_doubao_container`, ...). A file name registered
    as a module is the damage the user described, recorded in the registry.
    """
    if not _table_exists(conn, "module_registry"):
        return {"ok": False, "code": "NO_MODULE_REGISTRY"}
    rows = [dict(r) for r in conn.execute(
        "SELECT module_id, module_key, name, description FROM module_registry "
        "ORDER BY module_id")]
    junk = [r for r in rows if str(r["module_key"]).startswith("_")]
    real = [r for r in rows if not str(r["module_key"]).startswith("_")]
    return {"ok": True, "total": len(rows), "file_derived": len(junk),
            "real": len(real), "junk_keys": [str(r["module_key"]) for r in junk],
            "real_keys": [str(r["module_key"]) for r in real],
            "cite": ("measured: module_registry.module_key starts with '_' on "
                     "%d of %d rows" % (len(junk), len(rows)))}


# --------------------------------------------------------------------------
# WRITE the structure into code_location_registry (the "entity has file+line")
# --------------------------------------------------------------------------
def ensure_entity_location(conn: sqlite3.Connection, capability_key: str, *,
                           cite_ref: str) -> dict[str, Any]:
    """Record the derived file for the CHANNEL, MODULE and CAPABILITY entities.

    This is what makes \"entity has file name and line number\" TRUE for the three
    structural levels. It reuses `code_location_registry` — the table that
    already maps `(entity_type, entity_ref_id, version)` -> file + line — rather
    than adding a second location map.
    """
    s = structure_of(conn, capability_key)
    if not s["ok"]:
        return s
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    if not _table_exists(conn, "code_location_registry"):
        return {"ok": False, "code": "NO_LOCATION_TABLE"}
    cols = _columns(conn, "code_location_registry")
    written: list[dict[str, Any]] = []
    for kind, letter, _reg in STRUCTURE_LEVELS:
        stake: tuple[str, int, str] | None = None
        if kind == "channel":
            if s["channel_id"] is not None:
                stake = ("H", int(s["channel_id"]), s["channel_key"])
        elif kind == "module":
            stake = ("M", int(s["module_id"]), s["module_key"])
        else:
            stake = ("C", int(s["capability_id"]), s["capability_key"])
        if not stake:
            continue
        letter, ref_id, key = stake
        # EACH level gets ITS OWN derived path. The old code wrote the
        # capability's path for the channel and module rows too, which is
        # exactly the flat-path damage `location_mismatches` now reports
        # (5 active rows, measured 2026-09-27).
        if kind == "channel":
            fpath = "%s/" % s["channel_key"]
        elif kind == "module":
            fpath = "%s/%s/" % (s["channel_key"], s["module_key"])
        else:
            fpath = s["path"] + s["file_name"]
        ex = conn.execute(
            "SELECT location_id FROM code_location_registry WHERE "
            "entity_type=? AND entity_ref_id=? AND version=1", (letter, ref_id)
        ).fetchone()
        if ex:
            conn.execute("UPDATE code_location_registry SET file_path=?, "
                         "cite_ref=?, is_active=1, code_span=? WHERE location_id=?",
                         (fpath, cite_ref, kind, int(ex["location_id"])))
            written.append({"level": kind, "entity": "%s-%d" % (letter, ref_id),
                            "file": fpath, "created": False})
            continue
        conn.execute(
            "INSERT INTO code_location_registry (entity_type, entity_ref_id, "
            "version, file_path, line_start, line_end, code_span, cite_ref, "
            "is_active) VALUES (?,?,1,?,1,1,?,?,1)",
            (letter, ref_id, fpath, kind, cite_ref))
        written.append({"level": kind, "entity": "%s-%d" % (letter, ref_id),
                        "file": fpath, "created": True})
    conn.commit()
    return {"ok": True, "capability_key": capability_key, "written": written,
            "cite": s["cite"]}


def register_structure_terms(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the structural terms (the layout as DECLARED data)."""
    import terminology_registry as tr
    tr.ensure_schema(conn)
    out: list[dict[str, Any]] = []
    for key, kind, definition, cite in STRUCTURE_TERMS:
        r = tr.add_term(conn, key, definition=definition, cite_ref=cite,
                        term_kind=kind, is_active=1)
        out.append(dict(r, term_key=key))
    return {"ok": all(r.get("ok") for r in out), "terms": out,
            "registered": sum(1 for r in out if r.get("ok"))}


def location_mismatches(conn: sqlite3.Connection) -> dict[str, Any]:
    """ACTIVE `code_location_registry` rows whose path is NOT the derivation.

    This is the population the proof asserts is ZERO. A row is checked only
    when its entity can be resolved to a derivation; an unresolvable row is
    REPORTED (never silently passed), because a check that cannot see a row
    is not a check of that row.
    """
    if not _table_exists(conn, "code_location_registry"):
        return {"ok": False, "code": "NO_LOCATION_TABLE"}
    bad: list[dict[str, Any]] = []
    unresolvable: list[dict[str, Any]] = []
    checked = 0
    for r in conn.execute(
            "SELECT location_id, entity_type, entity_ref_id, file_path, "
            "is_active FROM code_location_registry WHERE is_active=1"):
        letter = str(r["entity_type"])
        ref = int(r["entity_ref_id"])
        got = str(r["file_path"] or "").strip().replace("\\", "/")
        want: str | None = None
        # The writer (`ensure_entity_location`) records the FULL derived path
        # for all three levels (channel folder, module folder, capability
        # file), so the check must expect the same full path — a check that
        # expects a different shape than the writer is a second truth.
        if letter == "C":
            row = conn.execute(
                "SELECT capability_key FROM capability_registry WHERE "
                "capability_id=?", (ref,)).fetchone()
            if row:
                s = structure_of(conn, str(row["capability_key"]))
                if s["ok"]:
                    want = s["full_path"]
        elif letter == "M":
            row = conn.execute(
                "SELECT m.module_key, ch.channel_key FROM module_registry m "
                "LEFT JOIN channel_registry ch ON ch.channel_id = "
                "m.channel_id WHERE m.module_id=?", (ref,)).fetchone()
            if row:
                want = "%s/%s/" % (str(row["channel_key"] or "NA"),
                                   str(row["module_key"]))
        elif letter == "H":
            row = conn.execute(
                "SELECT channel_key FROM channel_registry WHERE channel_id=?",
                (ref,)).fetchone()
            if row:
                want = "%s/" % str(row["channel_key"])
        if want is None:
            unresolvable.append({"location_id": int(r["location_id"]),
                                 "entity": "%s-%d" % (letter, ref),
                                 "file_path": got})
            continue
        checked += 1
        if got.rstrip("/") != want.rstrip("/"):
            bad.append({"location_id": int(r["location_id"]),
                        "entity": "%s-%d" % (letter, ref),
                        "file_path": got, "expected": want})
    return {"ok": True, "active_rows": checked + len(unresolvable),
            "checked": checked, "mismatches": bad,
            "unresolvable": unresolvable,
            "cite": "measured: every active code_location_registry row "
                    "re-derived via structure_of()"}


def _structured_prefixes(conn: sqlite3.Connection) -> list[str]:
    """Every derived capability folder, as a path prefix.

    A CODE (R) row is structured iff its file sits UNDER one of these —
    that is the only sense in which a code entity's location is DERIVED.
    A flat root path (`activation_gate.py`) is under none of them, so it is
    the damage, not a location to bless.
    """
    out: list[str] = []
    for r in conn.execute(
            "SELECT capability_key FROM capability_registry WHERE is_active=1"):
        s = structure_of(conn, str(r["capability_key"]))
        if s["ok"]:
            out.append(s["path"].rstrip("/"))
    return out


def activate_locations(conn: sqlite3.Connection) -> dict[str, Any]:
    """Activate the INACTIVE location rows whose path EQUALS the derivation.

    Only those. A row whose path does not equal the derivation stays
    inactive and is REPORTED — activating it would be hand-typing a path,
    the exact act the derivation exists to stop.

    For a CODE (R) row the derivation is different: the file must sit UNDER
    a derived capability folder. A flat root path is the damage, reported
    as `UNSTRUCTURED_CODE`, never activated.
    """
    if not _table_exists(conn, "code_location_registry"):
        return {"ok": False, "code": "NO_LOCATION_TABLE"}
    prefixes = _structured_prefixes(conn)
    activated: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for r in conn.execute(
            "SELECT location_id, entity_type, entity_ref_id, file_path "
            "FROM code_location_registry WHERE is_active=0"):
        letter = str(r["entity_type"])
        ref = int(r["entity_ref_id"])
        got = str(r["file_path"] or "").strip().replace("\\", "/")
        # CODE rows: structured iff under a derived capability folder.
        if letter == "R":
            if any(got.startswith(p + "/") for p in prefixes):
                conn.execute(
                    "UPDATE code_location_registry SET is_active=1, cite_ref=? "
                    "WHERE location_id=?",
                    ("measured: code file sits under a derived capability "
                     "folder", int(r["location_id"])))
                activated.append({"location_id": int(r["location_id"]),
                                  "entity": "R-%d" % ref, "file_path": got})
            else:
                refused.append({"location_id": int(r["location_id"]),
                                "entity": "R-%d" % ref,
                                "reason": "UNSTRUCTURED_CODE",
                                "file_path": got})
            continue
        want: str | None = None
        # Same full-path expectation as `location_mismatches` (one shape,
        # one truth — the writer's shape).
        if letter == "C":
            row = conn.execute(
                "SELECT capability_key FROM capability_registry WHERE "
                "capability_id=?", (ref,)).fetchone()
            if row:
                s = structure_of(conn, str(row["capability_key"]))
                if s["ok"]:
                    want = s["full_path"]
        elif letter == "M":
            row = conn.execute(
                "SELECT m.module_key, ch.channel_key FROM module_registry m "
                "LEFT JOIN channel_registry ch ON ch.channel_id = "
                "m.channel_id WHERE m.module_id=?", (ref,)).fetchone()
            if row:
                want = "%s/%s/" % (str(row["channel_key"] or "NA"),
                                   str(row["module_key"]))
        elif letter == "H":
            row = conn.execute(
                "SELECT channel_key FROM channel_registry WHERE channel_id=?",
                (ref,)).fetchone()
            if row:
                want = "%s/" % str(row["channel_key"])
        if want is None:
            refused.append({"location_id": int(r["location_id"]),
                            "entity": "%s-%d" % (letter, ref),
                            "reason": "UNRESOLVABLE_ENTITY"})
            continue
        if got.rstrip("/") == want.rstrip("/"):
            conn.execute(
                "UPDATE code_location_registry SET is_active=1, cite_ref=? "
                "WHERE location_id=?",
                ("measured: path equals the structure_contract derivation "
                 "for %s-%d" % (letter, ref), int(r["location_id"])))
            activated.append({"location_id": int(r["location_id"]),
                              "entity": "%s-%d" % (letter, ref),
                              "file_path": got})
        else:
            refused.append({"location_id": int(r["location_id"]),
                            "entity": "%s-%d" % (letter, ref),
                            "reason": "PATH_MISMATCH", "expected": want})
    conn.commit()
    unstructured = [x for x in refused if x["reason"] == "UNSTRUCTURED_CODE"]
    return {"ok": True, "activated": activated, "refused": refused,
            "activated_count": len(activated),
            "refused_count": len(refused),
            "unstructured_code_count": len(unstructured),
            "cite": "measured: only rows whose path EQUALS the derivation "
                    "were activated; the rest are reported, not forced"}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    terms = register_structure_terms(conn)
    locs: list[dict[str, Any]] = []
    for r in conn.execute("SELECT capability_key FROM capability_registry "
                          "WHERE is_active=1 ORDER BY capability_key"):
        res = ensure_entity_location(
            conn, str(r["capability_key"]),
            cite_ref="measured: structure_contract.structure_of() for %s"
                     % str(r["capability_key"]))
        locs.append({"capability_key": str(r["capability_key"]),
                     "ok": res.get("ok"), "levels": len(res.get("written", []))})
    return {"ok": True, "terms": terms, "locations": locs,
            "locations_written": sum(l["levels"] for l in locs),
            "retired": retired_capability_names(conn),
            "coverage": path_coverage(conn)}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    cov = path_coverage(conn)
    jm = junk_modules(conn)
    ret = retired_capability_names(conn)
    return {"ok": True,
            "declared_structure": "<channel>/<module>/<capability>/"
                                  "<module>_<capability>.py",
            "levels": [{"kind": k, "entity_letter": l, "register": r}
                       for k, l, r in STRUCTURE_LEVELS],
            "terms_registered": [r["term_key"] for r in conn.execute(
                "SELECT term_key FROM terminology_registry WHERE term_key IN "
                "('channel_folder','module_folder','capability_folder',"
                "'capability_file','api_file','function_file','table_file',"
                "'field_file')")] if _table_exists(
                    conn, "terminology_registry") else [],
            "coverage": {k: v for k, v in cov.items() if k != "rows"},
            "junk_modules": {k: v for k, v in jm.items() if k != "junk_keys"},
            "retired_names": {k: v for k, v in ret.items() if k != "retired"},
            "examples": [dict(r) for r in conn.execute(
                "SELECT c.capability_key, m.module_key, ch.channel_key "
                "FROM capability_registry c JOIN module_registry m ON "
                "m.module_id=c.module_id LEFT JOIN channel_registry ch ON "
                "ch.channel_id=m.channel_id WHERE c.is_active=1 "
                "ORDER BY c.capability_key LIMIT 4")],
            "location_rows_active": conn.execute(
                "SELECT COUNT(*) FROM code_location_registry WHERE is_active=1"
            ).fetchone()[0] if _table_exists(conn, "code_location_registry") else 0,
            "location_rows_total": conn.execute(
                "SELECT COUNT(*) FROM code_location_registry").fetchone()[0]
            if _table_exists(conn, "code_location_registry") else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--check-file", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.check_file:
            print(json.dumps(check_file(conn, args.check_file), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.coverage:
            cov = path_coverage(conn)
            print("capabilities=%d derivable=%d file_at_declared_path=%d "
                  "not_yet_moved=%d" % (cov["capabilities"], cov["derivable"],
                                        cov["file_exists_at_declared_path"],
                                        cov["not_yet_moved"]))
            for r in cov["rows"][:8]:
                print("   %-44s -> %-56s exists=%s"
                      % (r.get("capability_key"), r.get("expected"),
                         r.get("exists")))
            return 0
        if args.activate:
            res = activate_locations(conn)
            print("ACTIVATED (path == derivation): %d" % res["activated_count"])
            for a in res["activated"][:10]:
                print("   %-10s %s" % (a["entity"], a["file_path"]))
            print("REFUSED (reported, not forced): %d" % res["refused_count"])
            print("   of which UNSTRUCTURED_CODE (flat root path): %d"
                  % res["unstructured_code_count"])
            for f in res["refused"][:10]:
                print("   %-10s %s" % (f["entity"], f["reason"]))
            return 0
        if args.apply:
            res = apply(conn)
            print("STRUCTURE TERMS: %d registered"
                  % res["terms"]["registered"])
            for t in res["terms"]["terms"]:
                print("   %-20s %s" % (t["term_key"], t.get("code") or "ok"))
            print("LOCATIONS written (level rows): %d" % res["locations_written"])
            c = res["coverage"]
            print("COVERAGE: capabilities=%d file_at_declared_path=%d "
                  "not_yet_moved=%d" % (c["capabilities"],
                                        c["file_exists_at_declared_path"],
                                        c["not_yet_moved"]))
            return 0
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        print("DECLARED STRUCTURE: %s" % res["declared_structure"])
        for l in res["levels"]:
            print("   level %-12s entity letter %s  (%s)"
                  % (l["kind"], l["entity_letter"], l["register"]))
        print("structure terms registered: %s" % res["terms_registered"])
        c = res["coverage"]
        print()
        print("COVERAGE: capabilities=%d derivable=%d file_at_declared_path=%d "
              "not_yet_moved=%d" % (c["capabilities"], c["derivable"],
                                    c["file_exists_at_declared_path"],
                                    c["not_yet_moved"]))
        j = res["junk_modules"]
        print("MODULES: total=%d  file_derived_junk=%d  real=%d  (%s)"
              % (j["total"], j["file_derived"], j["real"], j["real_keys"]))
        print("code_location_registry: total=%d active=%d"
              % (res["location_rows_total"], res["location_rows_active"]))
        print()
        print("EXAMPLES (derived):")
        for e in res["examples"]:
            s = structure_of(conn, str(e["capability_key"]))
            print("   %-40s -> %s" % (e["capability_key"],
                                      s.get("path", "") + s.get("file_name", "")))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
