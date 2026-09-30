# -*- coding: utf-8 -*-
"""namespace_registry.py — layer B: the STRUCTURAL layer beneath capability.

WHY TWO LAYERS (measured, not preferred)
-----------------------------------------
The user chose option B after this measurement:

    49 API namespaces vs 15 capabilities declared (3.33 : 1)
    46 of 49 namespaces are dominated by ONE file (mouse_spot_helper.py)

And a hard constraint read out of `init_ontology_registry.sql:77,103`:

    api_registry.capability_id       INTEGER NOT NULL  FK -> capability_registry
    function_registry.capability_id  INTEGER NOT NULL  FK -> capability_registry

So every route needs a capability parent TODAY, and one cannot be derived: a
`file` binding would point 46 namespaces at the SAME file, which discriminates
nothing. That is why the two facts must live in two layers.

    layer 1  capability_registry   SEMANTIC     "verify the red cross is on the
                                                 target" — a GOAL, may span
                                                 namespaces, NOT provable
    layer 2  namespace_registry    STRUCTURAL   "/api/evidence" — a bounded HTTP
                                                 surface, PROVABLE from route
                                                 literals      (this module)

They are joined by ONE `capability_binding` row per namespace with
`subject_kind='namespace'`, so a judgement is recorded once per NAMESPACE
instead of once per ROUTE.

THE POINT OF THE SPLIT
----------------------
Layer 2 is provable, so it can be POPULATED without any judgement. Layer 1 is a
judgement, so it stays human-declared (`capability_binding`). This module builds
layer 2 and exposes the bridge; it does NOT decide which capability a namespace
serves.

Gates
-----
* a namespace row REQUIRES a `cite_ref` that names a real route literal
  (`file:line`). This is STRONGER than `citation_discipline`'s shape check on
  purpose: this layer exists to be provable, so "looks like path:line" is not
  enough — the cited line is OPENED and must hold `.route(`.
* a namespace whose dominant file maps to no module is REFUSED with the reason
  (file_stem rule), never inserted with a guessed parent.
* `apply(dry_run=True)` runs every gate in `derive()` BEFORE the dry-run branch,
  so a dry run cannot promise a row the real run refuses. An earlier module in
  this repo had a dry run that skipped a gate and promised 214 impossible rows.

Read/write: this module WRITES to the namespace layer. It never decides layer 1.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

import code_introspect as ci  # noqa: E402  (BASE_DIR first)

# (path, mtime_ns, size) -> the set of lines that DECLARE a route in that file.
# See `_assert_cited` check 4: without this the 49-citation check re-parses a
# 9.5k-line module 49 times and the Stop-hook report TIMES OUT.
_DECLARED_ROUTE_LINES: dict[tuple | None, set[int]] = {}

DDL = """
CREATE TABLE IF NOT EXISTS namespace_registry (
    namespace_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace_key  TEXT    NOT NULL UNIQUE,
    module_id      INTEGER NOT NULL,
    route_count    INTEGER NOT NULL DEFAULT 0,
    dominant_file  TEXT,
    cite_ref       TEXT    NOT NULL,
    description    TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version        TEXT    NOT NULL DEFAULT '1',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (module_id) REFERENCES module_registry (module_id)
);
CREATE INDEX IF NOT EXISTS idx_namespace_registry_module
    ON namespace_registry (module_id, is_active);
"""


class UncitedNamespace(RuntimeError):
    """A namespace proposal with no proof that the namespace exists."""


def log(msg: str) -> None:
    print("[namespace_registry] %s" % msg, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(DDL)
    conn.commit()
    return {"ok": True, "table": "namespace_registry"}


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def _assert_cited(cite_ref: str, *, root: Path | None = None) -> None:
    """The citation must be checkable AND the line must hold a route literal.

    Four checks, and the fourth is the load-bearing one:
      1. it has a line number
      2. the file exists
      3. the line number is within the file
      4. **the cited line really contains `.route(`**

    Check 4 is why this is not just `citation_discipline.assert_cited`: a
    structural row's entire claim is "this route literal exists at this line",
    so a citation that merely LOOKS well-formed proves nothing.
    """
    ref = str(cite_ref or "").strip()
    if ":" not in ref:
        raise UncitedNamespace("cite_ref %r is not a path:line" % cite_ref)
    fname, _, lineno = ref.rpartition(":")
    if not lineno.isdigit():
        raise UncitedNamespace("cite_ref %r has no line number" % cite_ref)
    p = (root or BASE_DIR) / fname
    if not p.is_file():
        raise UncitedNamespace("cite_ref %r names a file that does not exist"
                               % cite_ref)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    n = int(lineno)
    if not (1 <= n <= len(lines)):
        raise UncitedNamespace("cite_ref %r is past end of file (%d lines)"
                               % (cite_ref, len(lines)))
    # Check 4 is a DECLARATION question, so it is answered from the AST: a line
    # that merely contains the TEXT `.route(` proves nothing. Measured: a proof
    # assertion `not ci.has_code(HELPER, '@app.route("/api/case/')` contains
    # that text while DECLARING nothing, and a text test would have accepted it
    # as the citation for a route that no longer exists.
    #
    # The parse is MEMOISED per file. Measured defect: `_proof_namespace_registry`
    # parses 49 citations across `mouse_spot_helper.py`'s ~9.5k lines, and an
    # un-memoised `ast.parse` per citation made the proof TIMEOUT at 25s in the
    # Stop-hook report. A cache keyed on (mtime, size) is correct rather than
    # merely fast: a file edited mid-run changes both, so the cache cannot serve
    # a stale parse for a file that changed under it.
    key = None
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    declared_lines = _DECLARED_ROUTE_LINES.get(key)
    if declared_lines is None:
        try:
            declared_lines = {r["line"] for r in
                              ci.declared_routes("\n".join(lines))}
        except Exception:
            declared_lines = set()
        if key is not None:
            _DECLARED_ROUTE_LINES[key] = declared_lines
    if n not in declared_lines:
        raise UncitedNamespace(
            "cite_ref %r does not point at a route decorator: %r"
            % (cite_ref, lines[n - 1].strip()[:70]))


def resolve_module(conn: sqlite3.Connection, dominant_file: str | None) -> dict:
    """Map a dominant FILE to a module by name equality (file stem).

    `mouse_spot_helper.py` -> module_key `mouse_spot_helper`. Name equality is
    checkable evidence, and it is the SAME rule `hardcode_scope._resolve_module`
    uses, so the two cannot drift into disagreement.
    """
    if not dominant_file:
        return {"ok": False, "why": "no dominant file"}
    stem = Path(str(dominant_file)).stem
    row = conn.execute(
        "SELECT module_id, module_key FROM module_registry "
        "WHERE module_key = ? AND is_active = 1", (stem,)).fetchone()
    if row:
        return {"ok": True, "module_id": int(row["module_id"]),
                "module_key": row["module_key"], "via": "file_stem"}
    return {"ok": False,
            "why": "file %r has no module named %r (file_stem rule)"
                   % (dominant_file, stem)}


# ---------------------------------------------------------------------------
# derive — mechanically, from route literals
# ---------------------------------------------------------------------------


def derive(*, root: Path | str | None = None,
           db_path: Path | str | None = None) -> dict[str, Any]:
    """Propose one row per API namespace, each carrying its proof.

    Nothing is inserted here. `apply()` writes.
    """
    import namespace_map as nm

    root = Path(root or BASE_DIR)
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        m = nm.summary(root=root, db_path=db_path)
        proposed: list[dict] = []
        refused: list[dict] = []
        for ns_key, info in m["namespaces"].items():
            cite = info.get("dominant_citation")
            if not cite:
                refused.append({"namespace_key": ns_key, "gate": "citation",
                                "why": "no dominant citation for this namespace"})
                continue
            try:
                _assert_cited(cite, root=root)
            except UncitedNamespace as e:
                refused.append({"namespace_key": ns_key, "gate": "citation",
                                "why": str(e)})
                continue
            mod = resolve_module(conn, info.get("dominant_file_hypothesis"))
            if not mod["ok"]:
                refused.append({"namespace_key": ns_key, "gate": "module",
                                "why": mod["why"]})
                continue
            proposed.append({
                "namespace_key": ns_key,
                "module_id": mod["module_id"],
                "module_key": mod["module_key"],
                "route_count": int(info.get("routes") or 0),
                "dominant_file": info.get("dominant_file_hypothesis"),
                "cite_ref": cite,
                "description": "HTTP namespace %s (%d route(s))"
                               % (ns_key, int(info.get("routes") or 0)),
            })
        return {"ok": True, "proposed": proposed, "refused": refused,
                "proposed_count": len(proposed), "refused_count": len(refused),
                "gap": m["gap_statement"],
                "file_concentration": m["file_concentration"]}
    finally:
        conn.close()


def apply(*, root: Path | str | None = None,
          db_path: Path | str | None = None,
          dry_run: bool = True) -> dict[str, Any]:
    """Insert the proposed rows. Idempotent on `namespace_key`.

    Every gate runs in `derive()` BEFORE the dry-run branch, so a dry run cannot
    promise a row the real run would refuse.
    """
    d = derive(root=root, db_path=db_path)
    plan: dict[str, Any] = {"ok": True, "dry_run": dry_run,
                            "would_insert": len(d["proposed"]),
                            "would_refuse": len(d["refused"]),
                            "refused": d["refused"]}
    if dry_run:
        return plan

    conn = _connect(db_path)
    inserted = updated = 0
    try:
        ensure_schema(conn)
        now = _utc_now()
        for row in d["proposed"]:
            # re-assert at the WRITE site: a gate that only runs in the planner
            # is not a gate at the point of action.
            _assert_cited(row["cite_ref"], root=Path(root or BASE_DIR))
            existing = conn.execute(
                "SELECT namespace_id FROM namespace_registry "
                "WHERE namespace_key = ?", (row["namespace_key"],)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE namespace_registry SET module_id=?, route_count=?, "
                    "dominant_file=?, cite_ref=?, description=?, is_active=1, "
                    "updated_at=? WHERE namespace_id=?",
                    (row["module_id"], row["route_count"],
                     row["dominant_file"], row["cite_ref"], row["description"],
                     now, existing["namespace_id"]))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO namespace_registry (namespace_key, module_id, "
                    "route_count, dominant_file, cite_ref, description, "
                    "updated_at) VALUES (?,?,?,?,?,?,?)",
                    (row["namespace_key"], row["module_id"], row["route_count"],
                     row["dominant_file"], row["cite_ref"], row["description"],
                     now))
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    plan.update({"inserted": inserted, "updated": updated})
    return plan


# ---------------------------------------------------------------------------
# read + the bridge to layer 1
# ---------------------------------------------------------------------------


def list_namespaces(conn: sqlite3.Connection | None = None, *,
                    module_key: str | None = None,
                    db_path: Path | str | None = None) -> list[dict]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_schema(conn)
        sql = ("SELECT n.*, m.module_key FROM namespace_registry n "
               "JOIN module_registry m ON m.module_id = n.module_id "
               "WHERE n.is_active = 1")
        params: list[Any] = []
        if module_key:
            sql += " AND m.module_key = ?"
            params.append(module_key)
        sql += " ORDER BY n.route_count DESC, n.namespace_key"
        return [dict(r) for r in conn.execute(sql, tuple(params))]
    finally:
        if own:
            conn.close()


def get_namespace(namespace_key: str, *,
                  db_path: Path | str | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM namespace_registry "
                           "WHERE namespace_key = ?",
                           (str(namespace_key).strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def bind_namespace(conn: sqlite3.Connection, *, namespace_key: str,
                   capability_key: str, cite_ref: str,
                   declared_by: str | None = None,
                   note: str | None = None) -> dict[str, Any]:
    """THE BRIDGE: record that a structural namespace serves a semantic capability.

    Delegates to `capability_binding.declare` with `subject_kind='namespace'`, so
    the citation gate, the DECLARED/CONFIRMED/REJECTED lifecycle and the
    "a REJECTED binding is not revived" rule are REUSED rather than re-implemented.
    A second implementation of those rules is how the two copies drift apart.
    """
    ensure_schema(conn)
    row = conn.execute("SELECT namespace_id FROM namespace_registry "
                       "WHERE namespace_key = ? AND is_active = 1",
                       (str(namespace_key).strip(),)).fetchone()
    if not row:
        return {"ok": False,
                "why": "no active namespace %r — apply() it first"
                       % namespace_key}
    import capability_binding as cb
    if "namespace" not in cb.SUBJECT_KINDS:
        return {"ok": False, "gate": "subject_kind",
                "why": "capability_binding.SUBJECT_KINDS does not allow "
                       "'namespace' yet (%s); the CHECK constraint needs the "
                       "table rebuild migration first" % (cb.SUBJECT_KINDS,)}
    return cb.declare(conn, capability_key=capability_key,
                      subject_kind="namespace", subject_ref=namespace_key,
                      cite_ref=cite_ref, declared_by=declared_by, note=note)


def parent_for_namespace(conn: sqlite3.Connection, namespace_key: str) -> dict | None:
    """The CONFIRMED capability for a namespace, or None."""
    import capability_binding as cb
    if "namespace" not in cb.SUBJECT_KINDS:
        return None
    return cb.parent_for(conn, subject_kind="namespace",
                         subject_ref=str(namespace_key).strip())


def report(conn: sqlite3.Connection | None = None, *,
           db_path: Path | str | None = None) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_schema(conn)
        n = conn.execute("SELECT COUNT(*) FROM namespace_registry "
                         "WHERE is_active = 1").fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT m.module_key, COUNT(*) AS n FROM namespace_registry nr "
            "JOIN module_registry m ON m.module_id = nr.module_id "
            "WHERE nr.is_active = 1 GROUP BY m.module_key ORDER BY n DESC")]
        caps = conn.execute("SELECT COUNT(*) FROM capability_registry "
                            "WHERE is_active = 1").fetchone()[0]
        return {"ok": True, "namespaces": int(n), "capabilities": int(caps),
                "by_module": rows, "layers": 2,
                "note": "layer 2 (namespace) is provable and populated; layer 1 "
                        "(capability) is declared by a human"}
    finally:
        if own:
            conn.close()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if args.apply:
        res = apply(dry_run=False)
    elif args.derive:
        res = derive()
    elif args.report:
        res = report()
    else:
        res = apply(dry_run=True)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()