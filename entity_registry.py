# -*- coding: utf-8 -*-
"""
entity_registry.py — the DB-driven register of entity ID letters + versions.

Why this exists
---------------
The entity ID format is `{TYPE}-{register_id}-{version}`, e.g. `T-4323-1`:

    T        the type letter          -> which register holds the entity
    4323     the register's own id    -> DB-driven PK (AUTOINCREMENT memory)
    1        the version              -> a row in version_register

Two things that were MISSING (measured 2026-09-21) made that format unusable:

  1. There was NO version register anywhere. Tables containing "version" were
     only `version_center` and `skill_versions` -- neither is a general
     per-entity version chain. So the trailing `-1` was an unbacked number:
     `T-10-1` and `T-10-9` looked equally valid.
  2. `job_registry` and `event_registry` did not exist, so the declared letters
     `J` and `E` could not be issued at all.

And one decision from the user drives the whole design:

    entity ID and work/task ID are SEPARATE spaces -- never mixed.
    The register ids are DB-driven (DB is SSOT), high-water marks included.

So this module is the ALPHABET and the VERSION CHAIN, not a task allocator.
`track_id_audit` stays the task space; `task_entity_link` (see below) is the
edge between the two.

Letters are DATA, not code
--------------------------
`entity_type_register` is the SSOT for "which letter means which register".
Nothing here hard-codes a letter->table map at call time; the map is read from
the DB. Adding a kind is an INSERT, and removing one is a DELETE -- both are
visible, reviewable rows rather than a code edit.

The seed list below is only the INITIAL population. Once seeded, the DB wins.

Honest limits recorded here, not hidden
---------------------------------------
- `db_table_registry` owns the high-water marks it acquired during the
  taxonomy backfill, so real table ids start around 4323. `T-4323-1` is a
  correct ID under "DB is SSOT"; `T-10-1` will not occur for those rows.
- Several legacy registers carry a TEXT column named `version`
  (`db_table_registry.version` etc.). That is NOT the version chain. The chain
  is `version_register`. The two are deliberately not merged, because merging
  them would silently change what the legacy column means.
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

ENTITY_TYPE_DDL = """
CREATE TABLE IF NOT EXISTS entity_type_register (
    type_letter    TEXT    PRIMARY KEY,
    entity_kind    TEXT    NOT NULL UNIQUE,
    register_table TEXT    NOT NULL,
    pk_column      TEXT    NOT NULL,
    display_name   TEXT    NOT NULL,
    is_scope_level INTEGER NOT NULL DEFAULT 0
                   CHECK (is_scope_level IN (0, 1)),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

VERSION_REGISTER_DDL = """
CREATE TABLE IF NOT EXISTS version_register (
    version_register_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT    NOT NULL,
    entity_ref_id INTEGER NOT NULL,
    version       INTEGER NOT NULL CHECK (version >= 1),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    parent_version_id INTEGER,
    note          TEXT,
    created_by    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id, version),
    FOREIGN KEY (entity_type) REFERENCES entity_type_register (type_letter),
    FOREIGN KEY (parent_version_id) REFERENCES version_register (version_register_id)
);
CREATE INDEX IF NOT EXISTS idx_version_register_entity
    ON version_register (entity_type, entity_ref_id);
CREATE INDEX IF NOT EXISTS idx_version_register_active
    ON version_register (is_active, entity_type);
"""

# ---- REMOVED 2026-09-25 (plan_REMOVE.DEAD.EVENT.JOB) ----------------------
#
# `JOB_REGISTRY_DDL` and `EVENT_REGISTRY_DDL` were REMOVED. The human's ruling:
# "is old design, proof can remove -> remove rubbish".
#
# MEASURED, and it is why they are rubbish rather than merely empty:
#   * 0 rows each, and 0 `version_register` rows for letter E or J
#   * `job_register.py` (238 lines, 7 functions) was called ONLY by
#     `_proof_skill_tick.py` — NO product caller; `jobs_of` had NO caller at all
#   * `event_registry` had ONE reader, `_proof_entity_id.py`
#   * this file's own docstring said they "did not exist, so the declared
#     letters" could not be issued
#
# A register with no source and no reader is not a register; it is a table that
# makes the register count look larger than the system.

# The edge between the ENTITY space and the TASK space. One task may touch many
# entities ("add field D-55 to table T-4323" is two edges); one entity may be
# touched by many tasks. This is what makes the graph walkable without merging
# the two id spaces.
TASK_ENTITY_LINK_DDL = """
CREATE TABLE IF NOT EXISTS task_entity_link (
    link_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id      TEXT    NOT NULL,
    entity_type   TEXT    NOT NULL,
    entity_ref_id INTEGER NOT NULL,
    version       INTEGER,
    role          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (track_id, entity_type, entity_ref_id, version, role),
    FOREIGN KEY (entity_type) REFERENCES entity_type_register (type_letter)
);
CREATE INDEX IF NOT EXISTS idx_task_entity_link_task
    ON task_entity_link (track_id);
CREATE INDEX IF NOT EXISTS idx_task_entity_link_entity
    ON task_entity_link (entity_type, entity_ref_id);
"""

# THE ROW ID NEEDS NO REGISTER. THE HUMAN (2026-09-27), verbatim:
#   "`db_row_registry`, that is wrong, don't need that"
#   "example: Function = F / table_id = 10 = table ABC / row id = 11 =
#    function_register / version = 1 / will be F-10-11-1"
#
# The entity id is `{LETTER}-{table_id}-{row_id}-{version}`:
#   * `table_id` is `db_table_registry.db_table_id` of the letter's register
#   * `row_id`   is that register table's OWN PK (`pk_column`)
#
# A second register to name a row that ALREADY HAS a primary key is the "old and
# wrong design" the human rejected. `db_row_registry` was created 2026-09-21,
# dropped 2026-09-27, briefly restored the same day, and is now removed for good.

ALL_DDL = (ENTITY_TYPE_DDL, VERSION_REGISTER_DDL, TASK_ENTITY_LINK_DDL)

# INITIAL seed only. After this runs the DB is the SSOT -- adding or retiring a
# letter is an INSERT/DELETE on entity_type_register, never a code edit.
# `is_scope_level = 1` marks the letters that also name a level of the declared
# range (channel > module > capability > api > function > table > field).
ENTITY_TYPE_SEED: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("H", "channel",    "channel_registry",    "channel_id",    "channel",    1),
    ("M", "module",     "module_registry",     "module_id",     "module",     1),
    ("C", "capability", "capability_registry", "capability_id", "capability", 1),
    ("F", "function",   "function_registry",   "function_id",   "function",   1),
    ("A", "api",        "api_registry",        "api_id",        "api",        1),
    ("T", "db_table",   "db_table_registry",   "db_table_id",   "table",      1),
    ("D", "db_field",   "db_field_registry",   "db_field_id",   "field",      1),
    # ---- LETTERS J AND E REMOVED 2026-09-25 (plan_REMOVE.DEAD.EVENT.JOB) ----
    #
    # The human: "is old design, proof can remove -> remove rubbish".
    # MEASURED: `job_registry` and `event_registry` held 0 rows, 0
    # `version_register` rows, and `job_register.py` had NO product caller. A
    # letter that can never be issued is not an alphabet entry; it is a promise
    # the system does not keep.
    ("S", "skill",      "skill_register",      "skill_id",      "skill",      0),
)

# Letters added AFTER the first seed shipped.
#
# Why a separate list instead of just extending ENTITY_TYPE_SEED: the seeding
# rule is "populate only when the register is EMPTY", so that a human DELETE is
# not silently undone on the next startup (a proof asserts exactly that). An
# extended seed would therefore never reach an already-seeded DB.
#
# This list is applied by an explicit INSERT of the named letters, so both hold:
# a deleted v1 letter stays deleted, and a genuinely new letter gets added.
#
# Measured gap that motivated it: only 10 of the 19 `*_register` tables had a
# letter, so a system-wide key could not reach 7 real registers.
# (`entity_type_register` and `version_register` are META -- the alphabet and the
# version chain themselves -- and correctly have no letter.)
ENTITY_TYPE_ADDITIONS: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("R", "code",      "code_register",      "id",       "code register", 0),
    ("P", "prompt",    "prompt_register",    "prompt_id", "prompt",       0),
    ("U", "study",     "study_register",     "study_id",  "study",        0),
    ("W", "wording",   "wording_register",   "wording_id", "wording",      0),
    ("K", "workflow",  "workflow_register",  "workflow_id", "workflow",    0),
    ("Q", "test_case", "test_case_registry", "test_case_id", "test case",   0),
    # Layer B (2026-09-21): the STRUCTURAL HTTP surface. `is_scope_level = 0`:
    # namespace is NOT in `hardcode_scope.SCOPE_ORDER`, so it is NOT a taxonomy
    # level. It was seeded as `1` originally, and THAT was the breaking point —
    # it made `is_scope_level` carry two meanings ("is a taxonomy level" AND "is
    # a navigable structural layer"), so a reader could no longer tell which one
    # a `1` meant. The structural-layer fact belongs to namespace's OWN 5W1H
    # binding, not to the taxonomy flag.
    ("N", "namespace", "namespace_registry", "namespace_id", "namespace", 0),
    # LETTER `X` (db_row) IS REMOVED. THE HUMAN (2026-09-27):
    #   "`db_row_registry`, that is wrong, don't need that"
    #
    # It was added 2026-09-21 so that `T-1-5-1` could name a ROW of a table, and
    # it named a SECOND register for a row that already has a primary key. The
    # row id in `{LETTER}-{table_id}-{row_id}-{version}` is the register table's
    # OWN PK, so no letter and no register are needed for it.
    #
    # Two more registers that were reachable by NO system-wide id (measured
    # 2026-09-22 by `_proof_register_approval`, which reported 4 gaps).
    #
    # `component_register` is the VERDICT-PARSER register: 8 rows, each naming a
    # parser and an output schema. It has its own INTEGER `skill_id` PK and a
    # `skill_ref` FK, so it is an independent entity, not an alias of
    # `skill_register` — the two tables share the word "skill" but hold
    # different populations (the D3 defect recorded in
    # `/memories/repo/skill_identity_law.md`).
    ("B", "component", "component_register", "skill_id", "component", 0),
    # `skill_factor_register` is the 19-factor register. It already had an
    # INTEGER `factor_id` PK, so it was addressable in principle but not by an
    # entity id.
    ("G", "factor", "skill_factor_register", "factor_id", "factor", 0),
    # NOTE: letter `Z` was added for `case_register` on 2026-09-21 and REMOVED
    # the same day. The user's rule: a case is a MAPPING between a ticket and a
    # chat, and a mapping does not get an entity id of its own --
    # "case 唔應該有 Z-... -> remove". An entity id names a THING; minting one
    # for a mapping would make the mapping itself a thing, which is the mixing
    # the user rejected. `Y` is therefore still the unassigned letter.
    #
    # `Y` IS NOW ASSIGNED (2026-09-26), to `dimension_binding_register`.
    #
    # WHY IT QUALIFIES. The rule (`entity_letters_and_exemptions.md:39`) is: a
    # register gets a letter when it is an entity with an INTEGER PK. MEASURED:
    # `binding_id INTEGER PRIMARY KEY AUTOINCREMENT`, and `typeof(binding_id)` is
    # `integer` in every row. So it is an entity, not a MAPPING and not a
    # TEXT-PK DIMENSION axis.
    #
    # WHY IT NEEDED ONE. `activation_gate.two_part_verdict` reads an approval by
    # `(letter, ref_id)`. A binding's `ref_tag` is
    # `dimension_binding.{subject_kind}.{dimension_key}`, which resolved to
    # `kind='unknown'` -- so the gate could NEVER read a verdict for it and
    # refused for the WRONG reason ("no entity to read a 2-part verdict from")
    # instead of the RIGHT one ("no approval, no streak"). A gate that refuses
    # for the wrong reason cannot be satisfied by doing the right thing.
    #
    # `is_scope_level = 0`: a binding is not a taxonomy level. It is a CLAIM
    # about what a dimension MEANS for a subject kind.
    ("Y", "dimension_binding", "dimension_binding_register", "binding_id",
     "dimension binding", 0),
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def log(msg: str) -> None:
    print("[entity_registry] %s" % msg, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    if not _IDENT_RE.match(table or ""):
        return False
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def ensure_entity_registry_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the letters register, the version chain, J/E registers, and the edge.

    Idempotent. Seeds the letters only when the register is empty, so a human
    DELETE is not silently undone on the next startup. Letters added after the
    first seed are inserted EXPLICITLY (see ENTITY_TYPE_ADDITIONS) so that a
    deleted v1 letter stays deleted while a genuinely new letter still lands.
    """
    for ddl in ALL_DDL:
        conn.executescript(ddl)
    seeded = 0
    if conn.execute("SELECT COUNT(*) FROM entity_type_register").fetchone()[0] == 0:
        now = _utc_now()
        for letter, kind, table, pk, disp, scope in ENTITY_TYPE_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO entity_type_register "
                "(type_letter, entity_kind, register_table, pk_column, "
                " display_name, is_scope_level, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (letter, kind, table, pk, disp, scope, now, now))
            seeded += 1
    added = 0
    for letter, kind, table, pk, disp, scope in ENTITY_TYPE_ADDITIONS:
        # Only when the letter is absent AND its register really exists. A letter
        # whose table is missing would be a dangling entry, which the returned
        # `dangling` list would then have to report -- better not to create it.
        exists = conn.execute(
            "SELECT 1 FROM entity_type_register WHERE type_letter = ?",
            (letter,)).fetchone()
        if exists or not _table_exists(conn, table):
            continue
        now = _utc_now()
        conn.execute(
            "INSERT INTO entity_type_register "
            "(type_letter, entity_kind, register_table, pk_column, "
            " display_name, is_scope_level, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (letter, kind, table, pk, disp, scope, now, now))
        added += 1
    conn.commit()
    # ---- THE FLAG IS DERIVED, NOT STORED BY HAND (2026-09-23) --------------
    # `is_scope_level` USED to be a literal in the seed, and the `N` namespace
    # row was seeded as `1` even though namespace is NOT in
    # `hardcode_scope.SCOPE_ORDER` (`hardcode_scope.py:56-64`). That single row
    # made the column mean TWO things, so a reader could no longer tell which
    # one a `1` meant — the "mix the concept" the user named as the breaking
    # point. Deriving it from the ONE declaration makes drift impossible.
    reconciled = reconcile_scope_levels(conn)
    # Verify the seed actually points at real tables -- a letter whose register
    # is absent can never verify an ID, and that must be visible, not assumed.
    dangling = []
    for r in conn.execute("SELECT type_letter, register_table, pk_column "
                          "FROM entity_type_register WHERE is_active = 1"):
        if not _table_exists(conn, r["register_table"]):
            dangling.append({"letter": r["type_letter"],
                             "why": "register table %s absent" % r["register_table"]})
            continue
        cols = {c[1] for c in conn.execute(
            "PRAGMA table_info(%s)" % r["register_table"])}
        if r["pk_column"] not in cols:
            dangling.append({"letter": r["type_letter"],
                             "why": "pk column %s absent on %s"
                                    % (r["pk_column"], r["register_table"])})
    return {"ok": True, "seeded": seeded, "added": added, "dangling": dangling,
            "scope_levels_changed": reconciled["changed"],
            "letters": conn.execute(
                "SELECT COUNT(*) FROM entity_type_register").fetchone()[0]}


# ---------------------------------------------------------------------------
# `is_scope_level` IS DERIVED, NOT DECLARED (2026-09-23)
# ---------------------------------------------------------------------------
# WHY (the user's breaking point):
#
#   "problem is starting, should be channel / module / capability / api /
#    function / table / field, after we have expand it for skill.... may be this
#    is the destroy happen point. so is time to clear how does entity can have
#    it own define, not mix anymore"
#
# MEASURED: `is_scope_level = 1` marked EIGHT letters (A C D F H M N T) while
# `hardcode_scope.SCOPE_ORDER` holds SEVEN levels (`hardcode_scope.py:56-64`).
# `N` namespace was the eighth, seeded as `1` for the Layer B work. So the flag
# answered two different questions at once, and a reader could not tell which.
#
# THE FIX: the flag is not a hand-written literal. It is the answer to ONE
# question — "does this letter's `entity_kind` name one of the taxonomy levels?"
# — computed from `SCOPE_ORDER`. A derived value cannot drift from its source,
# which is what makes the mix impossible rather than merely discouraged.
def reconcile_scope_levels(conn: sqlite3.Connection) -> dict[str, Any]:
    """Set `is_scope_level` from `hardcode_scope.SCOPE_ORDER`. Idempotent.

    Returns `{ok, changed, levels, rows}` where `changed` is the number of rows
    whose stored value DISAGREED with the derived one (0 on a converged DB).
    The count is RETURNED rather than swallowed, so a run that had to correct
    drift says so instead of looking like a no-op.
    """
    import hardcode_scope as hs

    levels = tuple(hs.SCOPE_ORDER)
    changed = 0
    rows = []
    for r in conn.execute(
            "SELECT type_letter, entity_kind, is_scope_level "
            "FROM entity_type_register WHERE is_active = 1"):
        want = 1 if r["entity_kind"] in levels else 0
        if int(r["is_scope_level"]) != want:
            conn.execute(
                "UPDATE entity_type_register SET is_scope_level = ?, "
                "updated_at = ? WHERE type_letter = ?",
                (want, _utc_now(), r["type_letter"]))
            changed += 1
            rows.append({"letter": r["type_letter"], "kind": r["entity_kind"],
                         "was": int(r["is_scope_level"]), "now": want})
    conn.commit()
    return {"ok": True, "changed": changed, "levels": list(levels),
            "rows": rows}


def scope_level_letters(conn: sqlite3.Connection) -> list[str]:
    """The letters that ARE taxonomy levels, per the DERIVED flag."""
    return [r["type_letter"] for r in conn.execute(
        "SELECT type_letter FROM entity_type_register "
        "WHERE is_active = 1 AND is_scope_level = 1 ORDER BY type_letter")]


# ---------------------------------------------------------------------------
# letters are DATA
# ---------------------------------------------------------------------------


def get_entity_type(conn: sqlite3.Connection, letter: str) -> dict | None:
    """Look up a letter. Returns None for an unknown/inactive letter."""
    if not letter:
        return None
    row = conn.execute(
        "SELECT * FROM entity_type_register "
        "WHERE type_letter = ? AND is_active = 1", (str(letter).strip().upper(),)
    ).fetchone()
    return dict(row) if row else None


def list_entity_types(conn: sqlite3.Connection, *, scope_only: bool = False) -> list[dict]:
    sql = ("SELECT * FROM entity_type_register WHERE is_active = 1"
           + (" AND is_scope_level = 1" if scope_only else "")
           + " ORDER BY is_scope_level DESC, type_letter")
    return [dict(r) for r in conn.execute(sql)]


# ---------------------------------------------------------------------------
# the entity itself
# ---------------------------------------------------------------------------


def active_column(conn: sqlite3.Connection, table: str) -> tuple[str | None, str]:
    """Which column says an entity is live, and which VALUES count as active.

    Registers disagree, and assuming one shape breaks the others -- measured:
    `code_register` has NO `is_active` column; it says `status = 'active'` or
    `status = 'rubbish'`. Querying `is_active` there raised
    `OperationalError: no such column`. So the column is DISCOVERED, and the
    caller is told what was used rather than being left to assume.
    """
    if not _IDENT_RE.match(table or "") or not _table_exists(conn, table):
        return None, "table unavailable"
    cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}
    if "is_active" in cols:
        return "is_active", "is_active = 1"
    if "status" in cols:
        return "status", "status = 'active'"
    return None, "no active/status column"


def resolve_entity(conn: sqlite3.Connection, letter: str,
                   ref_id: int) -> dict | None:
    """Read the real register row for {letter, ref_id}. None if not active.

    The register/pk names come FROM the DB row, and are re-validated as
    identifiers before being interpolated, because they are used to build SQL.
    The active marker is discovered per register (see `active_column`).
    """
    et = get_entity_type(conn, letter)
    if not et:
        return None
    table, pk = et["register_table"], et["pk_column"]
    if not (_IDENT_RE.match(table) and _IDENT_RE.match(pk)):
        return None
    if not _table_exists(conn, table):
        return None
    col, clause = active_column(conn, table)
    sql = "SELECT * FROM %s WHERE %s = ?" % (table, pk)
    if col:
        sql += " AND " + clause
    row = conn.execute(sql, (int(ref_id),)).fetchone()
    return dict(row) if row else None


def entity_key_of(conn: sqlite3.Connection, letter: str, ref_id: int) -> str | None:
    """The human key of an entity (table_key, function_key, ...) when discoverable."""
    et = get_entity_type(conn, letter)
    row = resolve_entity(conn, letter, ref_id)
    if not et or not row:
        return None
    table, pk = et["register_table"], et["pk_column"]
    cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}
    # MEASURED 2026-09-27: `code_register` has NEITHER `code_key` NOR `name` --
    # its natural key is `file_path`. Without it `entity_key_of` returned None
    # for every letter R id, so `GET /api/entity/id-for` reported an id with no
    # key. A candidate list that omits the table's real key is a detector that
    # cannot find anything.
    for cand in ("%s_key" % et["entity_kind"].replace("db_", ""), "name",
                 "file_path"):
        if cand in cols:
            return row.get(cand)
    return None


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


def ensure_version(
    conn: sqlite3.Connection,
    letter: str,
    ref_id: int,
    version: int = 1,
    *,
    parent_version_id: int | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Register a version for an entity, but only if the entity really exists.

    A version row for a non-existent entity would make the ID verify while
    pointing at nothing, which is worse than a missing version.
    """
    et = get_entity_type(conn, letter)
    if not et:
        return {"ok": False, "why": "unknown letter %r" % letter}
    if not resolve_entity(conn, letter, int(ref_id)):
        return {"ok": False,
                "why": "no active %s entity with %s = %d"
                       % (letter, et["pk_column"], int(ref_id))}
    row = conn.execute(
        "SELECT version_register_id, version FROM version_register "
        "WHERE entity_type = ? AND entity_ref_id = ? AND version = ?",
        (et["type_letter"], int(ref_id), int(version))).fetchone()
    if row:
        return {"ok": True, "created": False,
                "version_register_id": row["version_register_id"],
                "version": row["version"]}
    cur = conn.execute(
        "INSERT INTO version_register (entity_type, entity_ref_id, version, "
        "parent_version_id, note, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (et["type_letter"], int(ref_id), int(version), parent_version_id,
         note, created_by))
    # ---- A NEW VERSION RETIRES THE ONE IT REPLACES ------------------------
    # MEASURED BUG (2026-09-27). THE HUMAN: "problem is proof run > new version
    # + and missing to is_active -> 0 for old version / that is BUG! fix it now".
    # MEASURED: this function inserted version N and touched nothing else, so
    # `G/39` ended up with versions 1 and 2 BOTH `is_active=1`.
    #
    # THE RETIRE IS NOT REIMPLEMENTED HERE. `activation_gate` is the ONE
    # definition of "which version is active" (it declares itself the only
    # writer of `is_active=1`), so this DELEGATES to it. A second
    # implementation would be two answers to one question — the defect class
    # this repo already names.
    #
    # Only for version > 1: version 1 has nothing older to retire, and calling
    # it would be a no-op that reads as if something happened.
    # The citation is THIS LINE — the one that creates the version being kept.
    # MEASURED: the citation gate accepts `path:line`, and REFUSES a register
    # row (`factor_registry.factor_id = 39` -> "not a checkable citation").
    retired: list[int] = []
    if int(version) > 1:
        try:
            import activation_gate as _ag
            res = _ag.retire_other_versions(
                conn, et["type_letter"], int(ref_id), keep_version=int(version),
                reason=("version %d supersedes it: version %d was registered for "
                        "%s/%d" % (int(version), int(version),
                                   et["type_letter"], int(ref_id))),
                cite_ref="entity_registry.py:537",
                decided_by=created_by or "ensure_version", commit=False)
            retired = res.get("retired") or []
        except ImportError:
            pass
    conn.commit()
    return {"ok": True, "created": True,
            "version_register_id": cur.lastrowid, "version": int(version),
            "retired": retired}


def get_version(conn: sqlite3.Connection, letter: str, ref_id: int,
                version: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM version_register "
        "WHERE entity_type = ? AND entity_ref_id = ? AND version = ? "
        "AND is_active = 1",
        (str(letter).strip().upper(), int(ref_id), int(version))).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# the ROW ID -- the register table's OWN PK. NO second register.
#
# THE HUMAN (2026-09-27): "`db_row_registry`, that is wrong, don't need that" /
# "example: Function = F / table_id = 10 = table ABC / row id = 11 =
# function_register / version = 1 / will be F-10-11-1".
#
# `register_row` / `get_row` / `row_owner_table_id` / `resolve_row` were removed
# with `db_row_registry`. A row that already has a primary key does not need a
# second register to name it.
# ---------------------------------------------------------------------------


def table_id_of_letter(conn: sqlite3.Connection, letter: str) -> int | None:
    """The `db_table_registry.db_table_id` of the table LETTER's register is.

    This is the SECOND part of `{LETTER}-{table_id}-{row_id}-{version}`.
    MEASURED: `function_registry` -> 38, `code_register` -> 1.
    """
    et = get_entity_type(conn, letter)
    if not et:
        return None
    t = conn.execute(
        "SELECT db_table_id FROM db_table_registry WHERE table_key = ? "
        "AND is_active = 1", (et["register_table"],)).fetchone()
    return int(t["db_table_id"]) if t else None


def row_exists(conn: sqlite3.Connection, letter: str,
               row_id: int) -> dict[str, Any]:
    """Does `row_id` name a row of the table LETTER's register is?

    This is the THIRD part of `{LETTER}-{table_id}-{row_id}-{version}`. The row
    id is the register table's OWN PK (`pk_column`), so the check is a lookup in
    that table -- there is no second register to consult.

    Returns `{ok, why, row_id, table_id, register_table, pk_column}`.
    """
    et = get_entity_type(conn, letter)
    if not et:
        return {"ok": False, "why": "unknown letter %r" % letter}
    tid = table_id_of_letter(conn, et["type_letter"])
    if tid is None:
        return {"ok": False,
                "why": "the register table %r is not itself registered"
                       % et["register_table"]}
    pk = str(et["pk_column"])
    if not _IDENT_RE.match(pk):
        return {"ok": False, "why": "unsafe pk_column %r" % pk}
    row = conn.execute(
        "SELECT 1 FROM %s WHERE %s = ?" % (et["register_table"], pk),
        (int(row_id),)).fetchone()
    if not row:
        return {"ok": False,
                "why": "no %s row with %s = %d"
                       % (et["register_table"], pk, int(row_id)),
                "table_id": tid, "register_table": et["register_table"],
                "pk_column": pk}
    return {"ok": True, "why": None, "row_id": int(row_id), "table_id": tid,
            "register_table": et["register_table"], "pk_column": pk}


def register_table_id(conn: sqlite3.Connection, letter: str) -> int | None:
    """The `db_table_registry.db_table_id` of the table a LETTER's register is.

    WHY THIS IS NOT `ref_id`
    ------------------------
    Measured 2026-09-21: `entity_type_register` says letter `S` resolves
    `ref_id` against `skill_register.skill_id`, and letter `T` resolves it
    against `db_table_registry.db_table_id`. So `ref_id` means a DIFFERENT
    thing per letter. A caller that needs the register TABLE must therefore
    look it up by the letter's register table, not by `ref_id`.

    Returns None when the register table is not itself registered.
    """
    et = get_entity_type(conn, letter)
    if not et:
        return None
    t = conn.execute(
        "SELECT db_table_id FROM db_table_registry WHERE table_key = ? "
        "AND is_active = 1", (et["register_table"],)).fetchone()
    return int(t["db_table_id"]) if t else None


# ---------------------------------------------------------------------------
# the GENERATOR -- mint an entity id in ONE call
# ---------------------------------------------------------------------------


def mint_entity(
    conn: sqlite3.Connection,
    letter: str,
    row_id: int,
    *,
    version: int = 1,
    note: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Mint an entity id, registering its version, and return the id string.

    WHY THIS EXISTS (measured 2026-09-21)
    -------------------------------------
    There was NO generator. A search for `register_entity` / `create_entity` /
    `allocate_entity` / `new_entity` / `mint_entity` / `issue_entity` /
    `generate_entity` returned zero hits. The only version writer was
    `ensure_version()`, which REFUSES unless the entity already exists -- so
    producing an id required a caller to insert the register row itself and
    then remember to stamp a version. Two steps, one of them easy to forget,
    and forgetting it makes the id unverifiable.

    This is the ONE call. It resolves the letter to its register, proves the
    entity exists, stamps the version, and returns the formatted id.

    `row_id` is the register table's OWN PK. THE HUMAN (2026-09-27):
    "`db_row_registry`, that is wrong, don't need that" / "example: Function = F
    / table_id = 10 = table ABC / row id = 11 = function_register / version = 1 /
    will be F-10-11-1".

    The id is `{LETTER}-{table_id}-{row_id}-{version}`:
      * `table_id` is `db_table_registry.db_table_id` of the letter's register
      * `row_id`   is that register table's OWN PK

    FAIL-CLOSED, in order:
      1. unknown letter            -> refuse (no register to point at)
      2. the register table is not itself registered -> refuse (no table_id)
      3. row does not exist in that table -> refuse (an id that verifies while
                                      pointing at nothing is worse than no id)
      4. version < 1               -> refuse
    """
    et = get_entity_type(conn, letter)
    if not et:
        return {"ok": False, "why": "unknown letter %r" % letter}
    if int(version) < 1:
        return {"ok": False, "why": "version must be >= 1"}

    tid = table_id_of_letter(conn, et["type_letter"])
    if tid is None:
        return {"ok": False,
                "why": "the register table %r is not itself registered"
                       % et["register_table"]}

    # ---- 3. the ROW, which IS the register table's own PK ----------------
    rr = row_exists(conn, et["type_letter"], int(row_id))
    if not rr.get("ok"):
        return {"ok": False, "why": rr.get("why")}

    v = ensure_version(conn, et["type_letter"], int(row_id), int(version),
                       note=note, created_by=created_by or "mint_entity")
    if not v.get("ok"):
        return {"ok": False, "why": v.get("why") or "version registration failed"}

    entity_id_str = "%s-%d-%d-%d" % (et["type_letter"], int(tid), int(row_id),
                                     int(version))

    return {
        "ok": True,
        "entity_id": entity_id_str,
        "letter": et["type_letter"],
        "entity_kind": et["entity_kind"],
        "register_table": et["register_table"],
        "table_id": int(tid),
        "row_id": int(row_id),
        "version": int(version),
        "version_created": bool(v.get("created")),
        "entity_key": entity_key_of(conn, et["type_letter"], int(row_id)),
    }


def entity_versions(conn: sqlite3.Connection, letter: str,
                    ref_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM version_register WHERE entity_type = ? "
        "AND entity_ref_id = ? ORDER BY version", (letter, int(ref_id)))]


def active_version(conn: sqlite3.Connection, letter: str,
                   ref_id: int) -> int | None:
    """Highest version. This is what `-{version}` means when unspecified."""
    row = conn.execute(
        "SELECT MAX(version) AS v FROM version_register "
        "WHERE entity_type = ? AND entity_ref_id = ? AND is_active = 1",
        (letter, int(ref_id))).fetchone()
    return row["v"] if row and row["v"] is not None else None


def backfill_version_one(conn: sqlite3.Connection, letter: str, *,
                         dry_run: bool = True) -> dict[str, Any]:
    """Give every existing entity its version 1, so `X-id-1` verifies.

    The citation for each row is the register row that proves the entity exists
    -- there is no other justification for the number 1.

    MEASURED DEFECT FIXED 2026-09-23: this function used to HARDCODE
    `WHERE is_active = 1`, which CRASHED on `code_register` (it has no
    `is_active`; it says `status = 'active'`) with
    `OperationalError: no such column: is_active`. The module already had the
    fix -- `active_column()` right above -- and its own docstring names that
    exact error. A rule that assumed ONE register shape and broke on the others
    is the same defect family as the location check accepting only the file
    form. So the active column is DISCOVERED, and what was used is REPORTED.
    """
    et = get_entity_type(conn, letter)
    if not et:
        return {"ok": False, "why": "unknown letter %r" % letter}
    table, pk = et["register_table"], et["pk_column"]
    if not (_IDENT_RE.match(table) and _IDENT_RE.match(pk)
            and _table_exists(conn, table)):
        return {"ok": False, "why": "register %s unavailable" % table}
    # DISCOVER the active marker. `col` is None when the register has neither
    # `is_active` nor `status` -- then EVERY row is taken, and that is REPORTED
    # so a register with no active concept is visible rather than silently 0.
    col, clause = active_column(conn, table)
    sql = "SELECT %s FROM %s" % (pk, table)
    if col:
        sql += " WHERE " + clause
    sql += " ORDER BY %s" % pk
    ids = [r[pk] for r in conn.execute(sql)]
    active_by = (col or "(none: ALL rows taken)")
    created = skipped = 0
    for rid in ids:
        exists = conn.execute(
            "SELECT 1 FROM version_register WHERE entity_type = ? "
            "AND entity_ref_id = ? AND version = 1",
            (et["type_letter"], rid)).fetchone()
        if exists:
            skipped += 1
            continue
        if dry_run:
            created += 1
            continue
        conn.execute(
            "INSERT INTO version_register (entity_type, entity_ref_id, version, "
            "note, created_by) VALUES (?, ?, 1, ?, ?)",
            (et["type_letter"], rid,
             "index-0 version, cited by %s.%s = %d (active by %s)"
             % (table, pk, rid, active_by),
             "backfill_version_one"))
        created += 1
    if not dry_run:
        conn.commit()
    return {"ok": True, "letter": et["type_letter"], "register": table,
            "active_by": active_by,
            "dry_run": dry_run, "entities": len(ids),
            "created": created, "skipped": skipped}


def backfill_version_one_all(conn: sqlite3.Connection, *,
                             dry_run: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    total = 0
    for et in list_entity_types(conn):
        res = backfill_version_one(conn, et["type_letter"], dry_run=dry_run)
        out[et["type_letter"]] = res
        total += int(res.get("created") or 0)
    return {"ok": True, "dry_run": dry_run, "total_created": total, "by_letter": out}


# ---------------------------------------------------------------------------
# the edge between entity space and task space
# ---------------------------------------------------------------------------


def link_task_entity(conn: sqlite3.Connection, track_id: str, letter: str,
                     ref_id: int, version: int | None = None,
                     role: str | None = None) -> dict[str, Any]:
    """Record that a TASK touches an ENTITY. The two id spaces stay separate."""
    if not resolve_entity(conn, letter, int(ref_id)):
        return {"ok": False, "why": "no active %s entity %d" % (letter, int(ref_id))}
    tid = str(track_id or "").strip()
    if not tid:
        return {"ok": False, "why": "track_id required"}
    row = conn.execute(
        "SELECT link_id FROM task_entity_link WHERE track_id = ? AND "
        "entity_type = ? AND entity_ref_id = ? AND IFNULL(version, -1) = "
        "IFNULL(?, -1) AND IFNULL(role, '') = IFNULL(?, '')",
        (tid, letter, int(ref_id), version, role)).fetchone()
    if row:
        return {"ok": True, "created": False, "link_id": row["link_id"]}
    cur = conn.execute(
        "INSERT INTO task_entity_link (track_id, entity_type, entity_ref_id, "
        "version, role) VALUES (?, ?, ?, ?, ?)",
        (tid, letter, int(ref_id), version, role))
    conn.commit()
    return {"ok": True, "created": True, "link_id": cur.lastrowid}


def entity_graph(conn: sqlite3.Connection, letter: str,
                 ref_id: int) -> dict[str, Any]:
    """Walk the parents of an entity: its versions and the tasks that touch it."""
    et = get_entity_type(conn, letter)
    if not et:
        return {"ok": False, "why": "unknown letter %r" % letter}
    L = et["type_letter"]
    tasks = [dict(r) for r in conn.execute(
        "SELECT track_id, version, role FROM task_entity_link "
        "WHERE entity_type = ? AND entity_ref_id = ? ORDER BY link_id",
        (L, int(ref_id)))]
    vers = entity_versions(conn, L, ref_id)
    parent_links = [v for v in vers if v.get("parent_version_id")]
    return {"ok": True, "letter": L, "ref_id": int(ref_id),
            "key": entity_key_of(conn, L, ref_id),
            "versions": vers, "version_lineage": parent_links,
            "tasks": tasks, "task_count": len(tasks)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensure", action="store_true",
                    help="create the schema in agent.db")
    ap.add_argument("--backfill-versions", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="for --backfill-versions: really write")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        out: dict[str, Any] = {}
        if args.ensure:
            out["ensure"] = ensure_entity_registry_schema(conn)
        out["letters"] = [{"letter": e["type_letter"],
                           "kind": e["entity_kind"],
                           "register": e["register_table"],
                           "scope_level": bool(e["is_scope_level"])}
                          for e in list_entity_types(conn)]
        if args.backfill_versions:
            out["backfill"] = backfill_version_one_all(conn,
                                                       dry_run=not args.apply)
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        else:
            if "ensure" in out:
                e = out["ensure"]
                print("schema: seeded=%d letters=%d dangling=%s"
                      % (e["seeded"], e["letters"], e["dangling"] or "none"))
            print("letters:")
            for x in out["letters"]:
                print("   %s  %-11s -> %-22s scope_level=%s"
                      % (x["letter"], x["kind"], x["register"], x["scope_level"]))
            if "backfill" in out:
                b = out["backfill"]
                print("version-1 backfill (dry_run=%s): total=%d"
                      % (b["dry_run"], b["total_created"]))
                for k, v in b["by_letter"].items():
                    if v.get("created"):
                        print("   %s %-22s created=%-5s skipped=%s"
                              % (k, v.get("register"), v.get("created"),
                                 v.get("skipped")))
    finally:
        conn.close()


if __name__ == "__main__":
    main()