# -*- coding: utf-8 -*-
"""subject_kind_registry.py — THE open set of subject kinds. One register.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "no, by ticket!! chat ticket, workflow ticket, task ticket and entity ticket
     so we can have middleware for 5W1H, not hardcode for rubbish"

MEASURED: the repo hardcodes the set of subject kinds in THREE places, each as a
SQL `CHECK` enum:

    onto_binding.bind_type            db_schema.py:2700-2712
      ('channel','module','version','task','register','tacid','table','other')
    capability_binding.subject_kind   capability_binding.py:58-107
      ('route','file','module','function','namespace')
    a third kind CHECK                db_schema.py:2674
      ('system','channel','module','function', ...)

`capability_binding.py:83` states the cost in the repo's own words:

    "SQLite cannot ALTER a CHECK constraint, so widening SUBJECT_KINDS requires a
     table rebuild."

So adding ONE kind ("chat", "workflow", "entity") means REBUILDING A TABLE. That
is the "hardcode for rubbish" the user rejected.

THE PATTERN ALREADY EXISTS IN THIS REPO — APPLY IT ONCE
-------------------------------------------------------
`dimension_binding_registry.py:36-40` states the correct split:

    * the FIXED set (the six 5W1H dimensions) is validated against a Python tuple
      (`skill_5w1h.DIMENSION_NAMES`) — a 7th cannot be introduced by an INSERT
    * the OPEN set (`subject_kind`) is FREE — an INSERT

This module is that open set, made explicit and shared, so the three CHECK enums
stop being the authority.

WHAT THIS MODULE IS NOT
-----------------------
It does NOT rebuild the existing tables. Their CHECK constraints stay in place
(that is out of scope for this task) and the divergence is RECORDED, not hidden:
`validate_kind()` is the authority going forward, and `check_divergence()`
reports which kinds a legacy CHECK would still reject.

REFUSALS
--------
  * an empty `kind_key`            -> refused
  * an empty `cite_ref`            -> refused ("no citation, no binding")
  * a `kind_key` that is not a lowercase identifier -> refused (a kind is a KEY,
    not a display name; `Chat` and `chat` must not become two kinds)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# A kind is a KEY: lowercase, digits, underscore. `Chat` and `chat` must not
# become two kinds, so the shape is enforced rather than normalised silently.
KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")

SUBJECT_KIND_DDL = """
CREATE TABLE IF NOT EXISTS subject_kind_registry (
    kind_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind_key      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT 'NA',
    -- WHICH TABLE holds the ref for this kind, when one exists. A kind whose
    -- ref is a plain integer (e.g. chat -> chat_main.id) leaves this 'NA'.
    ref_table     TEXT    NOT NULL DEFAULT 'NA',
    ref_column    TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_subject_kind_active
  ON subject_kind_registry (is_active, kind_key);
"""

# THE SEED. The four the user named, plus the kinds already in use across the
# three hardcoded enums (so the register is a SUPERSET of what exists today and
# nothing that works now stops working).
#
# (kind_key, display_name, description, ref_table, ref_column)
SUBJECT_KIND_SEED: tuple[tuple[str, str, str, str, str], ...] = (
    # --- the four the user named (2026-09-23) ---
    ("chat", "Chat",
     "a chat session; the ref is chat_main.id",
     "chat_main", "id"),
    ("workflow", "Workflow",
     "a workflow definition; the ref is workflow_registry.workflow_id",
     "workflow_registry", "workflow_id"),
    ("task", "Task",
     "a task instance; the ref is task_instances.task_id (TEXT)",
     "task_instances", "task_id"),
    ("entity", "Entity",
     "a registered entity; the ref is the entity id (e.g. T-1-5-1)",
     "NA", "NA"),
    ("entity_id", "Entity ID",
     "the entity ID SCHEME itself; the ref is entity_type_registry.type_letter",
     "entity_type_registry", "type_letter"),
    # --- kinds already in use (superset, so nothing regresses) ---
    # REPORTED FIRST, per the plan's allowlist rule ("edit ONLY if a
    # `subject_kind_registry` row is missing for a kind this module needs").
    # MEASURED 2026-09-24: `dimension_binding_registry` carries 6
    # `worker_identity` rows whose examples are `register:identity_registry:1`,
    # but this table had NO row for the kind — so the derived
    # `SUBJECT_registry` could not include it and the kind looked like a kind
    # with no register. `identity_registry.identity_id` is INTEGER, so the ref
    # is formable and the row belongs here.
    ("worker_identity", "Worker Identity",
     "a worker<->identity join; the ref is identity_registry.identity_id (the "
     "two ids live on identity_registry, joined by 5W1H — see "
     "worker_identity_binding.py:2)",
     "identity_registry", "identity_id"),
    # `prompt` ADDED 2026-09-27, for the SAME reason `worker_identity` was.
    #
    # MEASURED: `dimension_binding_registry` carries 6 `prompt` rows
    # (binding_id 397-402), but this table had NO row for the kind — so the
    # derived `SUBJECT_registry` could not include it, `example_ref('prompt')`
    # returned `''`, and `repair_examples` reported all 6 as `unmapped` with
    # `example='NA'`. MEASURED CONSEQUENCE: `_proof_binding_cite_source.py`
    # reported 5 failures ("EVERY example is a citation", "no example is the
    # bare string NA", ...) and `_proof_binding_example_source.py` reported 4.
    #
    # `prompt_registry.prompt_id` is INTEGER and the table holds 39 rows, so the
    # ref is formable and the row belongs here. A kind that is IN USE but not
    # REGISTERED is exactly the ambiguity this register exists to prevent.
    ("prompt", "Prompt",
     "a prompt; the ref is prompt_registry.prompt_id (the pk the `P` entity "
     "letter declares — see activation_gate.py:two_part_verdict)",
     "prompt_registry", "prompt_id"),
    ("module", "Module",
     "a module; the ref is module_registry.module_key (a KEY survives a "
     "rebuild; an id does not)",
     "module_registry", "module_key"),
    ("route", "Route",
     "an HTTP route literal", "NA", "NA"),
    ("file", "File",
     "a source file path", "NA", "NA"),
    ("function", "Function",
     "a function; the ref is function_registry.function_id",
     "function_registry", "function_id"),    ("namespace", "Namespace",
     "an API namespace; the ref is namespace_registry.namespace_id",
     "namespace_registry", "namespace_id"),
    # `table` / `field` REMOVED 2026-09-26 (human chose option A).
    #
    # MEASURED: `table` and `db_table` BOTH existed, both pointing at
    # `db_table_registry.db_table_id`; `field` and `db_field` likewise. Two names
    # for one thing is the ambiguity this register exists to prevent, and it made
    # `subject_kind_align.derived_renames` report a rename that could never be
    # applied — `kind_key` is UNIQUE, so `UPDATE ... SET kind_key='table' WHERE
    # kind_key='db_table'` raised IntegrityError.
    #
    # `db_table` / `db_field` are KEPT because they are the more precise names
    # (`table` collides with the SQL keyword) and they match the tables they
    # point at (`db_table_registry` / `db_field_registry`). `derived_renames`
    # already derived this direction from `entity_type_registry`, so the register
    # now AGREES with its own derivation instead of contradicting it.
    ("api", "API",
     "an API; the ref is api_registry.api_id",
     "api_registry", "api_id"),
    ("capability", "Capability",
     "a capability; the ref is capability_registry.capability_id",
     "capability_registry", "capability_id"),
    ("channel", "Channel",
     "a channel; the ref is channel_registry.channel_id",
     "channel_registry", "channel_id"),
    ("service", "Service",
     "a service; the ref is ticket_center.id",
     "ticket_center", "id"),
    ("version", "Version",
     "a version row; the ref is version_registry.version_registry_id",
     "version_registry", "version_registry_id"),
    ("register", "Register",
     "a register table itself", "NA", "NA"),
    ("tacid", "TACID",
     "a task-center id label", "NA", "NA"),
    ("system", "System",
     "the system itself (no ref)", "NA", "NA"),
    ("other", "Other",
     "an unclassified subject (explicit, so 'other' is a choice not a default)",
     "NA", "NA"),
)

# The legacy CHECK enums this register supersedes. Recorded so the divergence is
# VISIBLE: a kind in the register but not in a legacy CHECK would still be
# rejected by that table until it is rebuilt (out of scope for this task).
LEGACY_CHECKS: dict[str, tuple[str, ...]] = {
    "onto_binding.bind_type": (
        "channel", "module", "version", "task", "register", "tacid", "table",
        "other",
    ),
    "capability_binding.subject_kind": (
        "route", "file", "module", "function", "namespace",
    ),
}


class SubjectKindError(ValueError):
    """Raised when a kind cannot be registered."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(SUBJECT_KIND_DDL)
    conn.commit()
    return {"ok": True, "table": "subject_kind_registry"}


def add_kind(conn: sqlite3.Connection, kind_key: str, display_name: str = "",
             *, description: str = "NA", ref_table: str = "NA",
             ref_column: str = "NA", cite_ref: str = "",
             commit: bool = True) -> dict[str, Any]:
    """Register ONE kind. Idempotent on `kind_key`.

    REFUSES an empty key, a non-identifier key, and an empty `cite_ref` — the
    same "no citation, no binding" rule `dimension_binding_registry` applies.
    """
    key = str(kind_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_KIND_KEY",
                "message": "kind_key is required"}
    if not KIND_RE.match(key):
        return {"ok": False, "code": "BAD_KIND_KEY",
                "message": ("kind_key must be a lowercase identifier "
                            "(^[a-z][a-z0-9_]*$), got %r — a kind is a KEY, so "
                            "'Chat' and 'chat' must not become two kinds" % key)}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no kind: %s" % key}

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT kind_id FROM subject_kind_registry WHERE kind_key=?",
        (key,)).fetchone()
    if existing:
        return {"ok": True, "kind_id": int(existing[0]), "created": False,
                "kind_key": key}

    cur = conn.execute(
        "INSERT INTO subject_kind_registry (kind_key, display_name, description, "
        "ref_table, ref_column, cite_ref) VALUES (?,?,?,?,?,?)",
        (key, str(display_name or key).strip() or key, str(description or "NA"),
         str(ref_table or "NA"), str(ref_column or "NA"), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "kind_id": cur.lastrowid, "created": True,
            "kind_key": key}


def seed_kinds(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Insert the declared kinds. Idempotent."""
    ensure_schema(conn)
    added = 0
    for key, name, desc, rt, rc in SUBJECT_KIND_SEED:
        r = add_kind(conn, key, name, description=desc, ref_table=rt,
                     ref_column=rc,
                     cite_ref="subject_kind_registry.py:SUBJECT_KIND_SEED",
                     commit=False)
        if r.get("created"):
            added += 1
    if commit:
        conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) FROM subject_kind_registry").fetchone()[0]
    return {"ok": True, "added": added, "total": total}


# THE REFS THAT WERE CHANGED AWAY FROM THE SEED, and must be restored.
#
# MEASURED 2026-09-26: `subject_kind_align.REF_FIXES` rewrote FOUR live rows.
# THREE of them are WRONG, and each is wrong for a checkable reason:
#
#   chat    seed chat_main.id            -> align identity_registry.identity_id
#           WRONG: `chat_main` is the CHAT (the table the kind is named after).
#           `identity_registry` is the IDENTITY — a different kind, which
#           already has its own row (`worker_identity`).
#   task    seed task_instances.task_id  -> align dev_task.id
#           WRONG: `task_instances` EXISTS (measured) and its `task_id` is the
#           TEXT tracking code the seed names. `dev_task` is a DIFFERENT table.
#   module  seed module_registry.module_key -> align module_registry.module_id
#           WRONG: `ticket_store.map_module` STORES THE KEY
#           (`subject_ref_id=str(module)`, ticket_store.py:270) and every reader
#           JOINs `m.module_key = x.subject_ref_id` (ticket_store.py:311, 401).
#           So `_ref_exists` checked `module_id = 'task_center'` and REFUSED a
#           row that is correct. The align's stated justification — "resolve_subject
#           still accepts a key" — names a function that DOES NOT EXIST
#           (measured: `def resolve_subject` is defined NOWHERE).
#
#   version seed version_registry.id     -> align version_registry.version_registry_id
#           RIGHT: `version_registry.id` DOES NOT EXIST (measured); the pk is
#           `version_registry_id`. A ref to a missing column resolves nothing.
#           This one is KEPT.
#
# The fix is a MIGRATION, not a hand edit: the seed is the declaration, and the
# live row is restored to it. Idempotent.
REF_REVERTS: tuple[tuple[str, str, str], ...] = (
    ("chat", "chat_main", "id"),
    ("task", "task_instances", "task_id"),
    ("module", "module_registry", "module_key"),
)


def migrate_refs_to_seed(conn: sqlite3.Connection, *, commit: bool = True
                         ) -> dict[str, Any]:
    """Restore the live refs that were changed away from the seed. Idempotent."""
    ensure_schema(conn)
    done: list[dict[str, Any]] = []
    for (key, rt, rc) in REF_REVERTS:
        row = conn.execute(
            "SELECT ref_table, ref_column FROM subject_kind_registry "
            "WHERE kind_key=?", (key,)).fetchone()
        if not row:
            done.append({"kind_key": key, "reverted": False,
                         "why": "no row for this kind"})
            continue
        if (str(row[0]), str(row[1])) == (rt, rc):
            done.append({"kind_key": key, "reverted": False,
                         "why": "already agrees with the seed"})
            continue
        conn.execute(
            "UPDATE subject_kind_registry SET ref_table=?, ref_column=?, "
            "updated_at=datetime('now') WHERE kind_key=?", (rt, rc, key))
        done.append({"kind_key": key, "reverted": True,
                     "from": "%s.%s" % (row[0], row[1]),
                     "to": "%s.%s" % (rt, rc)})
    if commit:
        conn.commit()
    return {"ok": True, "reverts": done,
            "reverted_count": sum(1 for d in done if d.get("reverted")),
            "cite": ("measured: subject_kind_registry refs restored to "
                     "SUBJECT_KIND_SEED -> %s"
                     % [(d["kind_key"], d.get("to")) for d in done
                        if d.get("reverted")])}


# THE DUPLICATE KINDS REMOVED (human chose option A, 2026-09-26).
#
# MEASURED: `table` and `db_table` BOTH existed, both pointing at
# `db_table_registry.db_table_id`; `field` and `db_field` likewise. Two names for
# one thing is the ambiguity this register exists to prevent, and it made
# `subject_kind_align.derived_renames` report a rename that could NEVER be
# applied — `kind_key` is UNIQUE, so `UPDATE ... SET kind_key='table' WHERE
# kind_key='db_table'` raised IntegrityError.
#
# `db_table` / `db_field` are KEPT: they are the more precise names (`table`
# collides with the SQL keyword) and they match the tables they point at.
# `derived_renames` already derived this direction from `entity_type_registry`,
# so the register now AGREES with its own derivation.
#
# THE BINDINGS MOVE WITH THE KIND. MEASURED: `dimension_binding_registry` held 6
# rows for `table` and 6 for `field` (the derived 5W1H bindings). Deleting the
# kind without moving them would leave 12 bindings pointing at a kind that no
# longer exists — a dangling reference, which is the defect this repo refuses
# everywhere else. So the bindings are RE-KEYED to the surviving kind, and a
# binding that already exists under the survivor is left alone (the composite key
# is `(subject_kind, dimension_key)`).
DUPLICATE_KINDS: tuple[tuple[str, str], ...] = (
    ("table", "db_table"),
    ("field", "db_field"),
)


def migrate_duplicate_kinds(conn: sqlite3.Connection, *, commit: bool = True
                            ) -> dict[str, Any]:
    """Remove the duplicate kinds, RE-KEYING their bindings. Idempotent."""
    ensure_schema(conn)
    done: list[dict[str, Any]] = []
    for (dup, keep) in DUPLICATE_KINDS:
        row = conn.execute("SELECT kind_id, is_active FROM subject_kind_registry "
                           "WHERE kind_key=?", (dup,)).fetchone()
        if not row:
            done.append({"duplicate": dup, "kept": keep, "removed": False,
                         "why": "the duplicate kind is absent — already removed"})
            continue
        if not int(row[1] or 0):
            # ALREADY SOFT-DELETED. A second call must be a NO-OP, not a second
            # removal — otherwise the migration is not idempotent and every run
            # reports work it did not do.
            done.append({"duplicate": dup, "kept": keep, "removed": False,
                         "why": ("the duplicate kind is already inactive "
                                 "(is_active=0) — already removed")})
            continue
        if not conn.execute("SELECT 1 FROM subject_kind_registry WHERE "
                            "kind_key=?", (keep,)).fetchone():
            done.append({"duplicate": dup, "kept": keep, "removed": False,
                         "why": ("the SURVIVING kind %r is absent, so removing "
                                 "the duplicate would leave nothing" % keep)})
            continue
        # RE-KEY the bindings FIRST, so no binding is ever dangling.
        moved = 0
        rows = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='dimension_binding_registry'").fetchone():
            rows = [dict(r) for r in conn.execute(
                "SELECT binding_id, dimension_key FROM "
                "dimension_binding_registry WHERE subject_kind=?", (dup,))]
            for b in rows:
                clash = conn.execute(
                    "SELECT 1 FROM dimension_binding_registry WHERE "
                    "subject_kind=? AND dimension_key=?",
                    (keep, b["dimension_key"])).fetchone()
                if clash:
                    # The survivor already carries this dimension: the duplicate
                    # row is REDUNDANT, so it is deactivated rather than moved.
                    conn.execute(
                        "UPDATE dimension_binding_registry SET is_active=0, "
                        "updated_at=datetime('now') WHERE binding_id=?",
                        (int(b["binding_id"]),))
                else:
                    conn.execute(
                        "UPDATE dimension_binding_registry SET subject_kind=?, "
                        "updated_at=datetime('now') WHERE binding_id=?",
                        (keep, int(b["binding_id"])))
                    moved += 1
        # SOFT DELETE the kind: the repo's rule is soft delete only, so a removed
        # kind can be told from one that was never declared.
        conn.execute("UPDATE subject_kind_registry SET is_active=0, "
                     "updated_at=datetime('now') WHERE kind_key=?", (dup,))
        done.append({"duplicate": dup, "kept": keep, "removed": True,
                     "bindings_moved": moved, "bindings_total": len(rows)})
    if commit:
        conn.commit()
    return {"ok": True, "removals": done,
            "removed_count": sum(1 for d in done if d.get("removed")),
            "cite": ("measured: subject_kind_registry duplicate kinds soft-deleted "
                     "and their bindings re-keyed -> %s"
                     % [(d["duplicate"], d["kept"]) for d in done
                        if d.get("removed")])}


def validate_kind(conn: sqlite3.Connection, kind_key: str) -> dict[str, Any]:
    """THE authority: is this an active kind? Returns {ok, kind_key, ...}.

    Callers use this instead of a literal tuple, so adding a kind is an INSERT
    and never a table rebuild.
    """
    key = str(kind_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_KIND_KEY",
                "message": "kind_key is required"}
    ensure_schema(conn)
    row = conn.execute(
        "SELECT kind_id, display_name, ref_table, ref_column "
        "FROM subject_kind_registry WHERE kind_key=? AND is_active=1",
        (key,)).fetchone()
    if not row:
        known = [r[0] for r in conn.execute(
            "SELECT kind_key FROM subject_kind_registry WHERE is_active=1 "
            "ORDER BY kind_key")]
        return {"ok": False, "code": "UNKNOWN_SUBJECT_KIND",
                "message": ("subject_kind %r is not an active row in "
                            "subject_kind_registry (known: %s). An unknown kind "
                            "would make the mapping point at nothing."
                            % (key, ", ".join(known) or "none"))}
    return {"ok": True, "kind_key": key, "kind_id": int(row["kind_id"]),
            "display_name": str(row["display_name"]),
            "ref_table": str(row["ref_table"]),
            "ref_column": str(row["ref_column"])}


def list_kinds(conn: sqlite3.Connection, *, active_only: bool = True
               ) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql = ("SELECT kind_key, display_name, description, ref_table, ref_column, "
           "cite_ref, is_active FROM subject_kind_registry")
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY kind_key"
    return [dict(r) for r in conn.execute(sql)]


def check_divergence(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which registered kinds a LEGACY CHECK would still reject.

    Reported, never hidden. A kind listed here works through
    `validate_kind()` but would be refused by that table's CHECK until the table
    is rebuilt — which is out of scope for this task, so the gap is STATED.
    """
    ensure_schema(conn)
    registered = {r["kind_key"] for r in list_kinds(conn)}
    out: dict[str, Any] = {}
    for table_col, allowed in LEGACY_CHECKS.items():
        missing = sorted(registered - set(allowed))
        out[table_col] = {
            "legacy_allows": list(allowed),
            "registered_but_rejected_by_legacy_check": missing,
            "diverges": bool(missing),
        }
    return {"ok": True, "registered_kinds": sorted(registered),
            "legacy_checks": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--validate", default="")
    ap.add_argument("--divergence", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_kinds(conn), ensure_ascii=False))
            return 0
        if args.list:
            print(json.dumps({"ok": True, "kinds": list_kinds(conn)},
                             ensure_ascii=False, indent=2))
            return 0
        if args.validate:
            r = validate_kind(conn, args.validate)
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r.get("ok") else 1
        if args.divergence:
            print(json.dumps(check_divergence(conn), ensure_ascii=False,
                             indent=2))
            return 0
    finally:
        conn.close()

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
