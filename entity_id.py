# -*- coding: utf-8 -*-
"""
entity_id.py — parse, format and VERIFY `{LETTER}-{table_id}-{row_id}-{version}`.

    F-38-11-1    letter F, table 38 (function_registry), row 11, version 1
    R-1-75-1     letter R, table 1  (code_registry),     row 75, version 1

This is the ENTITY id space. It is NOT the task space:
    entity id  = which THING          (R-1-75-1)
    task id    = which WORK           (20.8, format {root}.{seq})
The user's rule is that the two are never mixed; `entity_registry.link_task_entity`
is the edge that connects them.

THE SHAPE IS STATED ONCE, HERE. `SHAPE` below is the only place the shape is
written. Every other file that needs to name the shape imports it. A shape
written in 38 files is 38 chances to write the OLD one, and that is exactly how
the mis-understanding kept coming back (measured 2026-09-27: 4 live files still
stated `{LETTER}-{ref_id}-{version}` after the rename).

The whole point of this module is that an ID is CHECKABLE, not merely
well-formed. `verify()` answers the question a string comparison cannot:

    F-38-11-1 and F-38-11-9 both LOOK like valid ids. Only a register lookup
    can say whether version 9 exists.

So `parse()` is cheap and syntactic, while `verify()` is the one that touches
the DB. A caller that only parses has proven nothing about existence, and the
result type says so (`exists: None` rather than `True`).

Unknown letters are rejected by LOOKUP, never by a hard-coded letter list, so
retiring or adding a kind is a row change in `entity_type_registry`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

import entity_registry as er

# {LETTER}-{table_id}-{row_id}-{version}, with optional surrounding whitespace.
#
# THE ENTITY ID (user, 2026-09-27):
#   "A"
#   "letter - table_id - row_id - version_id"
#   "3 is old, new version for entity is 4 part"
#   "version is the key to create mis-understand"
#   "`db_row_registry`, that is wrong, don't need that"
#   "example: Function = F / table_id = 10 = table ABC / row id = 11 =
#    function_registry / version = 1 / will be F-10-11-1"
#
#   F-38-11-1    letter F, table_id 38 (function_registry), row 11, version 1
#   R-1-75-1     letter R, table_id 1  (code_registry),     row 75, version 1
#
# WHY THE ROW PART EXISTS: with ONE trailing number, `F-38-11` is AMBIGUOUS --
# it reads as *version 11* OR *row 11*. The 4-part form NAMES both. That is the
# whole point, and it is why the 3-part form is OLD.
#
# NO `db_row_registry`: the row id IS the register table's OWN PK. A second
# register to name a row that already has a primary key is the "old and wrong
# design" the human rejected.
#
# THE SHAPE, STATED ONCE. Import this. Do not retype it. A retyped shape is how
# the old one survives a rename -- the code was renamed, the comments were not.
SHAPE = "{LETTER}-{table_id}-{row_id}-{version}"
SHAPE_EXAMPLE = "F-38-11-1"

# The OLD shapes, kept ONLY so a reader can be told "this is the old one".
# They are never accepted. `stale_statements()` searches for them.
OLD_SHAPES = (
    "{LETTER}-{ref_id}-{version}",
    "{LETTER}-{ref_id}-{row}-{version}",
)
_ID_RE = re.compile(
    r"^\s*([A-Za-z]{1,3})\s*-\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*$"
)

# A guard so a typo like "TT-1-1" is refused as malformed rather than looked up.
MAX_LETTER_LEN = 3


def parse(text: str) -> dict[str, Any]:
    """Syntactic parse only. `ok` means "shaped correctly", NOT "exists".

    `exists` and `reason` are filled by `verify()`.

    The ONLY accepted shape is `{LETTER}-{table_id}-{row_id}-{version}`. A
    3-part id is MALFORMED, not "an older form" -- there is ONE entity id
    format, and the 3-part form is the OLD one (user, 2026-09-27: "3 is old,
    new version for entity is 4 part").
    """
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "reason": "empty id", "exists": None,
                "letter": None, "table_id": None, "row_id": None,
                "version": None, "raw": raw}
    m = _ID_RE.match(raw)
    if not m:
        return {"ok": False,
                "reason": "malformed id %r; expected "
                          "{LETTER}-{table_id}-{row_id}-{version}" % raw,
                "exists": None, "letter": None, "table_id": None,
                "row_id": None, "version": None, "raw": raw}
    letter = m.group(1).upper()
    if len(letter) > MAX_LETTER_LEN:
        return {"ok": False, "reason": "letter too long: %r" % letter,
                "exists": None, "letter": letter, "table_id": None,
                "row_id": None, "version": None, "raw": raw}
    table_id = int(m.group(2))
    row_id = int(m.group(3))
    version = int(m.group(4))
    if table_id < 1:
        return {"ok": False, "reason": "table_id must be >= 1",
                "exists": None, "letter": letter, "table_id": table_id,
                "row_id": row_id, "version": version, "raw": raw}
    if row_id < 1:
        return {"ok": False, "reason": "row_id must be >= 1",
                "exists": None, "letter": letter, "table_id": table_id,
                "row_id": row_id, "version": version, "raw": raw}
    if version < 1:
        return {"ok": False, "reason": "version must be >= 1",
                "exists": None, "letter": letter, "table_id": table_id,
                "row_id": row_id, "version": version, "raw": raw}
    return {"ok": True, "reason": None, "exists": None, "letter": letter,
            "table_id": table_id, "row_id": row_id, "version": version,
            "raw": raw}


def format(letter: str, table_id: int, row_id: int, version: int) -> str:
    """Build an entity id. ALL FOUR parts are required.

    There is no 3-part overload: the 3-part form is the OLD one, and it is
    AMBIGUOUS -- `F-38-11` reads as *version 11* OR *row 11*. Naming both parts
    is what removes the ambiguity, so a defaulted `row_id` would reintroduce it.
    """
    L = (letter or "").strip().upper()
    if not L:
        raise ValueError("letter is required")
    if int(table_id) < 1:
        raise ValueError("table_id must be >= 1")
    if int(row_id) < 1:
        raise ValueError("row_id must be >= 1")
    if int(version) < 1:
        raise ValueError("version must be >= 1")
    return "%s-%d-%d-%d" % (L, int(table_id), int(row_id), int(version))


def verify(text: str, *, conn=None, db_path: Path | str | None = None) -> dict[str, Any]:
    """Full verification against the registers.

    Rules, all refusing rather than warning:
      1. shape must be `{LETTER}-{table_id}-{row_id}-{version}` (the ONLY shape)
      2. the letter must be an ACTIVE row in entity_type_registry
      3. `table_id` must be the `db_table_registry.db_table_id` of the table the
         letter's register is
      4. `row_id` must be a row of that table, by its OWN PK
      5. the version must be an ACTIVE row in version_registry

    THE ROW ID NEEDS NO SECOND REGISTER. THE HUMAN (2026-09-27):
    "`db_row_registry`, that is wrong, don't need that" / "example: Function = F
    / table_id = 10 = table ABC / row id = 11 = function_registry / version = 1 /
    will be F-10-11-1".

    There is no `require_version=False` escape any more. It existed to let a
    caller ask "does this entity exist, ignoring the version", which was only
    meaningful while an unversioned 2-part id was accepted. With one shape and
    four required parts, every part is checked or the id is not an id.
    """
    own = conn is None
    if own:
        conn = er._connect(db_path)
    try:
        p = parse(text)
        if not p["ok"]:
            return p

        letter, table_id, row_id, version = (p["letter"], p["table_id"],
                                            p["row_id"], p["version"])

        et = er.get_entity_type(conn, letter)
        if not et:
            p["ok"] = False
            p["exists"] = False
            p["reason"] = ("letter %r is not an active entity type "
                           "(see entity_type_registry)" % letter)
            p["known_letters"] = [e["type_letter"]
                                  for e in er.list_entity_types(conn)]
            return p

        # ---- 3. the TABLE the letter's register is ----------------------
        want_tid = er.table_id_of_letter(conn, letter)
        if want_tid is None:
            p["ok"] = False
            p["exists"] = False
            p["reason"] = ("the register table %r is not itself registered, "
                           "so no table_id can be checked"
                           % et["register_table"])
            return p
        if int(table_id) != int(want_tid):
            p["ok"] = False
            p["exists"] = False
            p["reason"] = ("table_id %d is not %s's table (db_table_id = %d)"
                           % (int(table_id), et["register_table"],
                              int(want_tid)))
            return p

        # ---- 4. the ROW, which IS the register table's own PK -----------
        rr = er.row_exists(conn, letter, int(row_id))
        if not rr.get("ok"):
            p["ok"] = False
            p["exists"] = False
            p["reason"] = rr.get("why")
            return p

        vr = er.get_version(conn, letter, int(row_id), int(version))
        if not vr:
            p["ok"] = False
            p["exists"] = False
            p["reason"] = ("no active version %d for %s-%d (versions present: %s)"
                           % (int(version), letter, int(row_id),
                              [v["version"] for v in
                               er.entity_versions(conn, letter, int(row_id))]
                              or "none"))
            return p

        p["exists"] = True
        p["entity_key"] = er.entity_key_of(conn, letter, int(row_id))
        p["register"] = et["register_table"]
        p["register_pk"] = et["pk_column"]
        p["table_checked"] = "yes"
        p["row_checked"] = "yes"
        p["version_registry_id"] = vr["version_registry_id"]
        p["version_checked"] = "yes"
        return p
    finally:
        if own:
            conn.close()


def require(text: str, *, conn=None, db_path: Path | str | None = None) -> dict:
    """verify() that RAISES. Use at a write site where an invalid id must stop."""
    res = verify(text, conn=conn, db_path=db_path)
    if not res.get("ok") or not res.get("exists"):
        raise InvalidEntityId("%s: %s" % (text, res.get("reason")))
    return res


class InvalidEntityId(RuntimeError):
    """An entity id that is malformed, unknown, or points at nothing."""


def normalize(text: str) -> str:
    """Canonical spacing/case for a VERIFIED id. Raises if malformed.

    All FOUR parts are preserved. The row part is what makes the id
    unambiguous, so dropping it would change what the id means.
    """
    p = parse(text)
    if not p["ok"]:
        raise InvalidEntityId(p["reason"] or "malformed entity id")
    return format(p["letter"], p["table_id"], p["row_id"], p["version"])


def explain(res: dict) -> str:
    if not res.get("ok"):
        out = ["INVALID %r" % res.get("raw")]
        out.append("  reason: %s" % res.get("reason"))
        if res.get("known_letters"):
            out.append("  known letters: %s" % ", ".join(res["known_letters"]))
        return "\n".join(out)
    out = ["VALID   %s" % format(res["letter"], res["table_id"],
                                res["row_id"], res["version"])]
    out.append("  letter      %s -> %s (%s)" % (res["letter"], res.get("register"),
                                                res.get("register_pk")))
    out.append("  table       %s (%s)" % (res.get("table_id"),
                                          res.get("table_checked")))
    out.append("  row         %s = %s" % (res.get("row_id"),
                                          res.get("entity_key")))
    out.append("  version     %s (%s)"
               % (res["version"], res.get("version_checked")))
    return "\n".join(out)


def stale_statements(root: Path | str | None = None) -> list[dict[str, Any]]:
    """Every LIVE file that still STATES an old entity-id shape.

    THE QUESTION THIS ANSWERS (human, 2026-09-27): "how can mis-understand
    disappear? by version? is 斷言 or old md or what / i happen again and again".

    Not by version, and not by deleting old markdown. A mis-understanding
    disappears when the NEXT reader cannot find the old shape stated as current.
    So this counts the places a reader CAN still find it, and it counts ONLY
    live code -- a historical plan that records what was true on its date is not
    a statement of the current shape, and deleting it would destroy the record
    of why the shape changed.

    A hit is a line that contains an OLD shape and does NOT, on the same line,
    mark it as old (`OLD`, `REMOVED`, `was`, `RETIRED`, `the old one`). A line
    that says "the old shape was X" is a correction, not a restatement.
    """
    root = Path(root) if root else BASE_DIR
    skip = {".venv", "node_modules", ".git", "dist", "qc_evidence",
            "__pycache__"}
    out: list[dict[str, Any]] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".py", ".js", ".vue"}:
            continue
        if any(part in skip for part in p.parts):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = str(p.relative_to(root))
        # This file DEFINES the old shapes so it can search for them. Its own
        # definition is not a statement that they are current.
        if rel.replace("\\", "/") == "entity_id.py":
            continue
        # A RETIRED proof is kept as a record. Its banner says so.
        if p.name.startswith("RETIRED_"):
            continue
        for n, line in enumerate(lines, 1):
            if not any(old in line for old in OLD_SHAPES):
                continue
            low = line.lower()
            if any(mark in low for mark in ("old", "removed", "was ", "retired",
                                            "superseded", "wrong")):
                continue
            out.append({"file": rel, "line": n, "text": line.strip()[:160]})
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*",
                    help="entity ids to verify, e.g. R-1-75-1")
    ap.add_argument("--stale", action="store_true",
                    help="list every live file that still states an OLD shape")
    args = ap.parse_args()
    if args.stale:
        hits = stale_statements()
        print("SHAPE (the only one): %s   e.g. %s" % (SHAPE, SHAPE_EXAMPLE))
        print("live files still stating an OLD shape: %d" % len(hits))
        for h in hits:
            print("  %s:%d  %s" % (h["file"], h["line"], h["text"]))
        raise SystemExit(1 if hits else 0)
    ids = args.ids or ["R-1-75-1", "F-38-11-1", "Z-1-1", "garbage",
                       "R-1-75", "R-1-75-1-9"]
    conn = er._connect()
    try:
        for i in ids:
            print(explain(verify(i, conn=conn)))
            print("-" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()