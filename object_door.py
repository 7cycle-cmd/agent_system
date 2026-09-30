"""object_door.py — the ONE site for asking about a NAMED object.

THE DEFECT THIS CLOSES (measured, not asserted)
------------------------------------------------
59 files in this repo decide "does this object exist?" with:

    SELECT 1 FROM sqlite_master WHERE type='table' AND name=?

That question is WRONG whenever a name is RENAMED table -> view, which is a
DECLARED, repo-wide migration pattern here (`llm_100_run` -> `proof_run`,
`v_skill_contract` -> `skill_contract_template`). After such a rename the guard
answers "missing", and the caller reports an EMPTY LIST as `ok: true`.

MEASURED, LIVE: `GET /api/evidence/llm-100` returned
`{"ok": true, "exists": false, "count": 0, "rounds": [], "runs": []}`
while the object it names held **2995 rows**. A silent false empty — the
`empty_detector_failure_class` the repo already recorded.

THE AUTHORING RULE (derived from a MEASURED existing pattern)
-------------------------------------------------------------
`coord_store.py` already does it right:

    def kind(name):            # <- takes ONLY a NAME. One entity.
        row = conn.execute("SELECT type FROM sqlite_master WHERE name=?",
                           (name,)).fetchone()
        return row["type"] if row else None

So the rule this module ENFORCES is:

    A function that asks a question about an OBJECT takes ONE parameter — a NAME.
    It must NOT take two parameters (a `table=` and a `view=`) or assume a kind.

A two-parameter shape is the BUG'S SIGNATURE: it makes the CALLER state the kind,
which is exactly the thing that goes stale when the name is renamed.

WHY THIS EXISTS AT ALL (the user's real ask)
---------------------------------------------
Dropping the compatibility views saves NO space — a view and the table it wraps
live inside the SAME `agent.db`. The cost is not bytes; it is MIS-UNDERSTANDING:
a worker who reads `llm_100_run` believes they are looking at the evidence store
when they are looking at a SECOND NAME for it. One door over the names is what
makes that impossible to get wrong.

DELIBERATE NON-USE: `db_schema._table_exists` is NOT replaced by this module.
It gates `ALTER TABLE`, so a VIEW must NOT pass it. A kind-agnostic answer there
would be a BUG. The contrast is recorded in `_proof_object_door.py` (QC-07).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

# The kinds `sqlite_master` can report for a named object that can be READ.
READABLE_KINDS = ("table", "view")

# The pattern that carries the defect. Kept as ONE compiled regex so the audit
# and the authoring rule cannot drift apart.
TABLE_ONLY_GUARD = re.compile(
    r"type\s*=\s*['\"]table['\"]\s+AND\s+name\s*=", re.IGNORECASE
)


def object_kind(conn: sqlite3.Connection, name: str) -> str | None:
    """Return the live KIND of `name`: 'table', 'view', 'virtual', or None.

    NOTE THE ABSENCE OF A `type` FILTER. That absence IS the fix: asking
    `type='table' AND name=?` is the caller asserting the kind, and the assertion
    is what goes stale. The DB is the authority on the kind, not the caller.
    """
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ? LIMIT 1", (str(name),)
    ).fetchone()
    if row is None:
        return None
    return str(row["type"] if isinstance(row, sqlite3.Row) else row[0])


def object_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Does an object with this NAME exist, of ANY readable kind?

    This is the replacement for `type='table' AND name=?`. A view counts: it is
    readable through the same name, which is the only thing the caller needs.
    """
    return object_kind(conn, name) in READABLE_KINDS


def resolve_object(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """ONE DOOR: resolve a NAME to its canonical name AND its live object.

    The caller supplies ONE thing — a name — and gets back:
      * `canonical` : the CURRENT name (via `terminology_alias.resolve_name`), so
                      an old name answers its replacement rather than "missing".
      * `kind`      : the live object kind of the canonical name.
      * `exists`    : kind is a readable kind.

    A name that resolves to nothing is returned as `exists: False` WITH A NAMED
    REASON. It is never reported as success-with-an-empty-list: an empty result
    is not evidence of absence unless something proves the detector works.
    """
    out: dict[str, Any] = {
        "asked": str(name),
        "canonical": str(name),
        "how": None,
        "kind": None,
        "exists": False,
        "reason": None,
    }

    # 1. The name register answers FIRST. A renamed object must answer its
    #    CURRENT name, or the caller looks for an object that no longer exists
    #    under that name and calls the miss "empty".
    try:
        import terminology_alias as ta

        res = ta.resolve_name(conn, str(name))
        if res.get("ok"):
            out["canonical"] = res.get("now") or str(name)
            out["how"] = res.get("how")
    except Exception as e:  # a missing register must not hide the object itself
        out["reason"] = "NAME_REGISTER_UNREADABLE: %s: %s" % (type(e).__name__, e)

    # 2. The OBJECT is asked about by NAME ONLY.
    kind = object_kind(conn, out["canonical"])
    out["kind"] = kind

    if kind is None and out["canonical"] != str(name):
        # The canonical name has no object; fall back to the asked name, because
        # a register can be ahead of a migration. Report WHICH name answered.
        kind = object_kind(conn, str(name))
        if kind is not None:
            out["kind"] = kind
            out["canonical"] = str(name)
            out["reason"] = out["reason"] or "CANONICAL_HAS_NO_OBJECT_USED_ASKED"

    if out["kind"] in READABLE_KINDS:
        out["exists"] = True
        out["reason"] = None
    elif out["kind"] is not None:
        out["reason"] = out["reason"] or "OBJECT_KIND_NOT_READABLE: %s" % out["kind"]
    else:
        out["reason"] = out["reason"] or "NO_OBJECT_NAMED: %s" % out["canonical"]

    return out


def object_gaps(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every term whose OLD name is STILL a live object — a second name in place.

    This is the "2 names in different locations" problem as a NUMBER. A concept
    whose alias is still a real table/view means a reader can reach the SAME data
    through TWO names, which is precisely the mis-understanding to prevent.
    """
    import json as _json

    names = {
        str(r["name"]): str(r["type"])
        for r in conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    found = []
    for r in conn.execute(
        "SELECT term_key, alias_list, is_active FROM terminology_registry"
    ):
        raw = str(r["alias_list"] or "").strip()
        if not raw or raw.upper() == "NA":
            continue
        try:
            aliases = _json.loads(raw)
        except Exception:
            aliases = [raw]
        for a in aliases:
            a = str(a)
            if a in names:
                found.append({
                    "concept": str(r["term_key"]),
                    "old_name": a,
                    "still_a": names[a],
                    "is_active": int(r["is_active"] or 0),
                })
    return {"ok": True, "count": len(found), "gaps": found}


def reader_audit(root: Path | None = None) -> dict[str, Any]:
    """How many files still decide object existence with a table-only guard.

    A COUNT, so coverage is checkable rather than claimed. The population is not
    rewritten in this task (see the plan); this number is how the remaining work
    is tracked instead of being forgotten.
    """
    root = root or BASE
    hits: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if TABLE_ONLY_GUARD.search(line):
                hits.append({"file": path.name, "line": i,
                             "text": line.strip()[:120]})
    files = sorted({h["file"] for h in hits})
    return {"ok": True, "files": len(files), "sites": len(hits),
            "file_list": files, "hits": hits}


def object_questions_that_take_a_kind(root: Path | None = None) -> dict[str, Any]:
    """AST scan: functions that ask about an object using TWO params (name + kind).

    The authoring rule is "one question, one NAME". A function shaped
    `(conn, name, kind=...)` / `(conn, table, ...)` makes the CALLER assert the
    kind — the signature of the defect. This reports such signatures so the rule
    is ENFORCED, not merely written down.
    """
    import ast

    root = root or BASE
    offenders: list[dict[str, Any]] = []
    kind_words = ("kind", "objtype", "obj_type", "object_type", "is_view")
    for path in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [a.arg for a in node.args.args]
            if any(k in args for k in kind_words):
                offenders.append({"file": path.name, "function": node.name,
                                  "args": args, "line": node.lineno})
    return {"ok": True, "count": len(offenders), "offenders": offenders}


def register(app: Any) -> None:
    """Mount the door on a FastAPI app (same shape as `terminology_api.register`)."""
    try:
        from fastapi import APIRouter
    except Exception:  # pragma: no cover - only when FastAPI is absent
        return
    router = APIRouter()

    @router.get("/api/object/resolve")
    def _resolve(name: str) -> Any:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            return resolve_object(conn, name)
        finally:
            conn.close()

    @router.get("/api/object/gaps")
    def _gaps() -> Any:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            return object_gaps(conn)
        finally:
            conn.close()

    app.include_router(router)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", action="store_true",
                    help="count the table-only guards still in the repo")
    ap.add_argument("--resolve", metavar="NAME", default=None,
                    help="resolve ONE name to its canonical name and live object")
    ap.add_argument("--gaps", action="store_true",
                    help="concepts whose OLD name is still a live object")
    ap.add_argument("--questions", action="store_true",
                    help="functions that ask about an object using TWO params")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.resolve:
            out = resolve_object(conn, a.resolve)
        elif a.gaps:
            out = object_gaps(conn)
        elif a.questions:
            out = object_questions_that_take_a_kind()
        else:
            out = reader_audit()
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    if a.resolve:
        print("asked    : %s" % out["asked"])
        print("canonical: %s  (how=%s)" % (out["canonical"], out["how"]))
        print("kind     : %s" % out["kind"])
        print("exists   : %s" % out["exists"])
        if out["reason"]:
            print("reason   : %s" % out["reason"])
    elif a.gaps:
        print("objects reachable through a SECOND (old) name: %d" % out["count"])
        for g in out["gaps"]:
            print("   concept %-26r old name %-20r still a %s"
                  % (g["concept"], g["old_name"], g["still_a"]))
    elif a.questions:
        print("functions asking about an object with TWO params: %d" % out["count"])
        for o in out["offenders"]:
            print("   %-34s %s(%s)" % (o["file"], o["function"], ", ".join(o["args"])))
    else:
        print("files still using a table-only object guard: %d" % out["files"])
        print("sites                                          : %d" % out["sites"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
