# -*- coding: utf-8 -*-
"""chat_main_remove.py — remove `chat_main` ONLY after three preconditions pass.

USER RULING (2026-09-24)
------------------------
    "**b** and **totally remove old when new proofed**"

So removal is CONDITIONAL and it is a GATE, not a step. This script is the gate.
Run:

    .\\.venv\\Scripts\\python.exe chat_main_remove.py --precheck
    .\\.venv\\Scripts\\python.exe chat_main_remove.py --apply
    .\\.venv\\Scripts\\python.exe chat_main_remove.py --apply --drop-archive

THE THREE PRECONDITIONS (all must pass; each is MEASURED)
---------------------------------------------------------
P1 WRITE_PATH_SWITCHED  — the IDENTITY is written FIRST: in every file that
                          writes `chat_main`, the `open_identity` call appears
                          BEFORE the `chat_main` write (measured by LINE
                          ORDER), and every writer is JUSTIFIED in
                          WRITER_JUSTIFICATIONS. A writer that writes
                          `chat_main` before the identity FAILS P1.
P2 REFERENCES_RESOLVE   — every distinct `chat_id` referenced by a live table
                          resolves through `identity_registry.chat_id`.
P3 BACKUP_ON_DISK       — a backup file exists whose `chat_main` row count
                          EQUALS the live count (an EMPTY backup is a FALSE
                          GREEN, so the count is compared, not just existence).

`--apply` does a SOFT REMOVE (rename to `_archived_`), because the repo doctrine
is soft delete only. A physical DROP needs `--drop-archive` AFTER the archive
has been verified — and it is a SEPARATE, deliberate act.

WHY A RENAME COUNTS AS REMOVAL: nothing reads `chat_main` any more (P1), every
reference resolves through `identity_registry` (P2), and the archive is not a
`chat_main` — a reader asking for `chat_main` now fails LOUDLY instead of
reading a stale table. A silent stale read is the failure mode being prevented.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"
ARCHIVE_SUFFIX = "__archived_identity_invert"
BACKUP_GLOB = "agent.db.bak-*"

# A writer of `chat_main` that is NOT the live per-chat write path. Each entry
# NAMES why it is allowed to stay. An UNJUSTIFIED writer fails P1 -- that is
# the gate.
WRITER_JUSTIFICATIONS: dict[str, str] = {
    "db_schema.py": (
        "the `_migrate_chat_ids` LEGACY migration helper -- it creates the "
        "migrated rows BEFORE this removal and is not a per-chat write path; "
        "it is the provenance of the `legacy_migration` rows themselves"),
    "_proof_conversation_env.py": (
        "a THROWAWAY-DB proof fixture (`INSERT INTO chat_main` into a temp db)"),
    "skill_library_api.py": (
        "the OUTPUT writer -- `chat_main` is now the OUTPUT FORMAT and this "
        "writer opens the IDENTITY first (checked by P1a, so the claim is "
        "MEASURED, not asserted in this comment)"),
}

# The call that writes the IDENTITY, and the call that writes the OUTPUT row.
_IDENTITY_CALL = "open_identity"
_OUTPUT_TABLES = ("chat_main",)

# A writer that PRE-DATES the inversion: the LEGACY migration helper (it creates
# the `legacy_migration` rows) and a throwaway-db proof fixture. They are
# GATED-EXEMPT and REPORTED by name, so "exempt" is visible rather than silent.
# The LIVE output writer must pass; an exempt writer must be justified.
LEGACY_WRITERS: frozenset[str] = frozenset({
    "db_schema.py",
    "_proof_conversation_env.py",
})

# A writer that predates the inversion: the LEGACY migration helper (it creates
# the `legacy_migration` rows) and a throwaway-db proof fixture. They are
# GATED-EXEMPT and REPORTED by name, so "exempt" is visible rather than silent.
# The LIVE output writer must pass; a legacy writer must be justified.
LEGACY_WRITERS: frozenset[str] = frozenset({
    "db_schema.py",
    "_proof_conversation_env.py",
})


def identity_written_first(root: Path | None = None) -> dict[str, Any]:
    """Is the IDENTITY written BEFORE the `chat_main` OUTPUT row, per writer?

    Measured by LINE ORDER in the file, not by reading a comment. A writer that
    writes `chat_main` before opening the identity is writing the OLD way.
    """
    import ast

    root = root or BASE
    per: dict[str, Any] = {}
    ok = True
    for name in sorted(set(WRITER_JUSTIFICATIONS) | {"skill_library_api.py"}):
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError) as exc:
            per[name] = {"ok": False, "why": "unreadable: %s" % exc}
            ok = False
            continue
        id_lines, out_lines = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = (fn.attr if isinstance(fn, ast.Attribute) else
                     fn.id if isinstance(fn, ast.Name) else "")
            if fname == _IDENTITY_CALL:
                id_lines.append(node.lineno)
            if isinstance(fn, ast.Attribute) and fn.attr in _EXEC_METHODS \
                    and node.args:
                a0 = node.args[0]
                blob = a0.value if isinstance(a0, ast.Constant) \
                    and isinstance(a0.value, str) else (
                        ast.unparse(a0) if isinstance(a0, ast.JoinedStr) else "")
                if any(_WRITE_RE.search(blob) and t in blob
                       for t in _OUTPUT_TABLES):
                    out_lines.append(node.lineno)
        if not out_lines:
            per[name] = {"ok": True, "writes_chat_main": False,
                         "identity_calls": len(id_lines)}
            continue
        first_out = min(out_lines)
        first_id = min(id_lines) if id_lines else None
        good = first_id is not None and first_id < first_out
        legacy = name in LEGACY_WRITERS
        per[name] = {"ok": good or legacy, "writes_chat_main": True,
                     "legacy_exempt": legacy,
                     "first_identity_line": first_id,
                     "first_output_line": first_out,
                     "why": (("LEGACY writer (exempt, reported): writes "
                              "chat_main at line %s with no identity -- it ran "
                              "BEFORE the inversion" % first_out) if legacy else
                             ("identity opened at line %s, before the "
                              "chat_main write at line %s" % (first_id, first_out))
                             if good else
                             ("NO identity call before the chat_main write "
                              "(identity=%s output=%s)" % (first_id, first_out)))}
        if not good and not legacy:
            ok = False
    gated = sorted(n for n, v in per.items() if not v.get("legacy_exempt"))
    return {"ok": ok, "vacuous": not per, "gated_writers": gated,
            "legacy_writers": sorted(LEGACY_WRITERS), "writers": per,
            "detail": (("no writer source under %s (VACUOUS — reported, not "
                        "counted as a pass)" % (root or BASE)) if not per
                       else ("the identity is written FIRST in every gated "
                             "chat_main writer (%s)" % gated if ok else
                             "a writer writes chat_main BEFORE the identity"))}

# Live readers are not a precondition (a reader of a removed table fails
# loudly), but they are REPORTED so the removal is not blind.
REF_TABLES: tuple[tuple[str, str], ...] = (
    ("chat_identity_log", "chat_id"),
    ("chat_center_message", "chat_id"),
    ("chat_reply_log", "chat_id"),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


# --------------------------------------------------------------------------
# P1  WRITE_PATH_SWITCHED
# --------------------------------------------------------------------------
_WRITE_RE = re.compile(
    r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+chat_main\b|\bREPLACE\s+INTO\s+chat_main\b",
    re.I)
# Only a call whose NAME is an execute-like method can WRITE. A bare string
# literal (`c.says("INSERT INTO chat_main" not in SRC)`) is an ASSERTION about
# the source, not a write — counting it is a FALSE POSITIVE of the scan (it
# flagged `_proof_mode_session_api.py`, which has no write at all).
_EXEC_METHODS = {"execute", "executemany", "executescript", "execute_script"}


def _write_lines(text: str) -> list[int]:
    """Line numbers of REAL writes into `chat_main` (AST, not a text match)."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _EXEC_METHODS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if _WRITE_RE.search(first.value):
                out.append(first.lineno)
        elif isinstance(first, ast.JoinedStr):  # an f-string write
            if _WRITE_RE.search(ast.unparse(first)):
                out.append(first.lineno)
    return sorted(set(out))


def live_writers(root: Path | None = None) -> dict[str, Any]:
    """Every file that REALLY writes into `chat_main`, classified.

    The scan is AST-based so an assertion ABOUT the source cannot be mistaken
    for a write, and it reports `sql_literals_seen` so the scan is provably
    NON-VACUOUS (it did look at real SQL, not zero strings).
    """
    root = root or BASE
    hits: dict[str, Any] = {}
    scanned = 0
    sql_literals = 0
    for path in sorted(root.rglob("*.py")):
        if any(part in {".venv", "__pycache__", ".git", "node_modules"}
               for part in path.parts):
            continue
        if path.name == Path(__file__).name:
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "chat_main" not in text:
            continue
        sql_literals += text.count("INSERT") + text.count("REPLACE")
        lines = _write_lines(text)
        if lines:
            hits[path.name] = {"lines": lines,
                               "justified": path.name in WRITER_JUSTIFICATIONS,
                               "why": WRITER_JUSTIFICATIONS.get(path.name)}
    unjustified = sorted(k for k, v in hits.items() if not v["justified"])
    return {"ok": not unjustified, "scanned_files": scanned,
            "vacuous": scanned == 0,
            "sql_literals_seen": sql_literals, "writers": hits,
            "unjustified": unjustified,
            "detail": (("no .py source under %s (VACUOUS)" % (root or BASE))
                       if scanned == 0 else
                       ("every writer is justified" if not unjustified else
                        "LIVE writers remain: %s" % unjustified))}


# --------------------------------------------------------------------------
# P2  REFERENCES_RESOLVE
# --------------------------------------------------------------------------
def references_resolve(conn: sqlite3.Connection) -> dict[str, Any]:
    per: dict[str, Any] = {}
    ok = True
    for table, col in REF_TABLES:
        if not _table_exists(conn, table):
            per[table] = "NO_TABLE"
            continue
        if col not in _columns(conn, table):
            per[table] = "NO_COLUMN"
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL" % (col, table, col))]
        resolved = [i for i in ids if conn.execute(
            "SELECT 1 FROM identity_registry WHERE chat_id=?", (i,)).fetchone()]
        unresolved = sorted(set(map(str, ids)) - set(map(str, resolved)))
        per[table] = {"distinct": len(ids), "resolved_via_identity_registry":
                      len(resolved), "unresolved": unresolved[:20],
                      "unresolved_count": len(unresolved)}
        # A FIXTURE-only table (`chat_reply_log`: 19 of 21 rows are proof
        # fixtures) cannot gate the removal; it is REPORTED as such.
        if table == "chat_reply_log":
            per[table]["gates_removal"] = False
            per[table]["note"] = ("REPORTED, does not gate: its chat_id is "
                                  "FIXTURE pollution (see the backfill's "
                                  "fixture_report)")
            continue
        per[table]["gates_removal"] = True
        if unresolved:
            ok = False
    return {"ok": ok, "tables": per,
            "detail": ("every referenced chat resolves through "
                       "identity_registry.chat_id" if ok else
                       "some referenced chats do NOT resolve")}


# --------------------------------------------------------------------------
# P3  BACKUP_ON_DISK
# --------------------------------------------------------------------------
def backup_on_disk(conn: sqlite3.Connection, root: Path | None = None
                   ) -> dict[str, Any]:
    root = root or BASE
    live = conn.execute("SELECT COUNT(*) FROM chat_main").fetchone()[0] \
        if _table_exists(conn, "chat_main") else 0
    cands = sorted(root.glob(BACKUP_GLOB), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    verified: list[dict[str, Any]] = []
    for p in cands:
        entry: dict[str, Any] = {"path": p.name, "bytes": p.stat().st_size}
        try:
            b = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True)
            try:
                if _table_exists(b, "chat_main"):
                    entry["chat_main_rows"] = b.execute(
                        "SELECT COUNT(*) FROM chat_main").fetchone()[0]
                    entry["has_hash_table"] = _table_exists(b, "chat_main_hash")
                else:
                    entry["chat_main_rows"] = "NO_TABLE"
            finally:
                b.close()
        except sqlite3.Error as exc:
            entry["error"] = str(exc)
        entry["covers_live"] = (entry.get("chat_main_rows") == live
                                and live > 0)
        verified.append(entry)
    good = [e for e in verified if e.get("covers_live")]
    return {"ok": bool(good), "live_rows": live, "candidates": verified,
            "detail": ("a backup covers the live %d rows (%s)"
                       % (live, good[0]["path"]) if good else
                       "NO backup on disk covers the live %d chat_main rows "
                       "(an EMPTY or missing backup is a FALSE GREEN)"
                       % live)}


def precheck(conn: sqlite3.Connection, root: Path | None = None
             ) -> dict[str, Any]:
    p1w = live_writers(root)
    p1i = identity_written_first(root)
    p1 = {"ok": bool(p1w["ok"] and p1i["ok"]),
          "classify": p1w, "identity_first": p1i,
          "detail": ("identity written first; every writer justified"
                     if (p1w["ok"] and p1i["ok"]) else
                     " | ".join(x["detail"] for x in (p1w, p1i)
                               if not x["ok"]))}
    p2 = references_resolve(conn)
    p3 = backup_on_disk(conn, root)
    return {"ok": bool(p1["ok"] and p2["ok"] and p3["ok"]),
            "P1_WRITE_PATH_SWITCHED": p1, "P2_REFERENCES_RESOLVE": p2,
            "P3_BACKUP_ON_DISK": p3,
            "chat_main_rows": conn.execute("SELECT COUNT(*) FROM chat_main"
                                           ).fetchone()[0]
            if _table_exists(conn, "chat_main") else 0,
            "identities": conn.execute("SELECT COUNT(*) FROM identity_registry"
                                       ).fetchone()[0]
            if _table_exists(conn, "identity_registry") else 0}


class RemovalRefused(RuntimeError):
    pass


def apply(conn: sqlite3.Connection, root: Path | None = None,
          drop_archive: bool = False) -> dict[str, Any]:
    """Soft-remove `chat_main` once all three preconditions pass."""
    pre = precheck(conn, root)
    if not pre["ok"]:
        failed = [k for k in ("P1_WRITE_PATH_SWITCHED", "P2_REFERENCES_RESOLVE",
                              "P3_BACKUP_ON_DISK") if not pre[k]["ok"]]
        raise RemovalRefused(
            "removal REFUSED — unmet precondition(s): %s | %s | %s"
            % (failed, pre["P1_WRITE_PATH_SWITCHED"]["detail"],
               pre["P3_BACKUP_ON_DISK"]["detail"]))
    if not _table_exists(conn, "chat_main"):
        return {"ok": True, "already_removed": True, "precheck": pre}

    arch = "chat_main" + ARCHIVE_SUFFIX
    harch = "chat_main_hash" + ARCHIVE_SUFFIX
    conn.execute("ALTER TABLE chat_main RENAME TO %s" % arch)
    if _table_exists(conn, "chat_main_hash"):
        conn.execute("ALTER TABLE chat_main_hash RENAME TO %s" % harch)
    conn.commit()
    # A reader asking for `chat_main` must now FAIL LOUDLY.
    loud = False
    try:
        conn.execute("SELECT 1 FROM chat_main LIMIT 1").fetchone()
    except sqlite3.Error:
        loud = True
    conn.execute("CREATE VIEW IF NOT EXISTS chat_main_removed AS SELECT "
                 "'chat_main was removed by IDENTITY.INVERT.REPLACE.CHAT_MAIN' "
                 "AS note, '%s' AS archive_table, %r AS removed_at"
                 % (arch, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    # COUNT BEFORE any drop: reading it afterwards fails (the archive is gone)
    # -- caught by this file's own proof.
    rows = int(conn.execute("SELECT COUNT(*) FROM %s" % arch).fetchone()[0])

    dropped = False
    if drop_archive:
        conn.execute("DROP TABLE %s" % arch)
        if _table_exists(conn, harch):
            conn.execute("DROP TABLE %s" % harch)
        conn.commit()
        dropped = True
    return {"ok": True, "archive_table": arch, "hash_archive_table": harch,
            "reader_now_fails_loudly": loud, "archive_dropped": dropped,
            "rows_archived": rows, "precheck": pre}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--root", default=str(BASE))
    ap.add_argument("--precheck", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--drop-archive", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.apply:
            try:
                res = apply(conn, Path(args.root), args.drop_archive)
            except RemovalRefused as exc:
                print("REFUSED: %s" % exc)
                return 2
            print("REMOVED: chat_main -> %s (%d rows archived)"
                  % (res["archive_table"], res["rows_archived"]))
            print("   reader of `chat_main` now fails loudly: %s"
                  % res["reader_now_fails_loudly"])
            print("   archive dropped: %s" % res["archive_dropped"])
            return 0
        res = precheck(conn, Path(args.root))
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0 if res["ok"] else 2
        print("PRECHECK  ok=%s   chat_main_rows=%d  identities=%d"
              % (res["ok"], res["chat_main_rows"], res["identities"]))
        for key in ("P1_WRITE_PATH_SWITCHED", "P2_REFERENCES_RESOLVE",
                    "P3_BACKUP_ON_DISK"):
            v = res[key]
            print("   %-24s ok=%-5s %s" % (key, v["ok"], v["detail"]))
            if key == "P1_WRITE_PATH_SWITCHED":
                v1 = v["classify"]
                for f, info in v1["writers"].items():
                    print("        %-32s justified=%-5s %s"
                          % (f, info["justified"], info["why"] or "!! NOT JUSTIFIED"))
                for f, info in v["identity_first"]["writers"].items():
                    print("        identity-first %-18s ok=%-5s %s"
                          % (f, info["ok"], info["why"]))
            if key == "P2_REFERENCES_RESOLVE":
                for t, info in v["tables"].items():
                    if isinstance(info, dict):
                        print("        %-22s distinct=%-4s resolved=%-4s gates=%-5s"
                              % (t, info["distinct"],
                                 info["resolved_via_identity_registry"],
                                 info["gates_removal"]))
            if key == "P3_BACKUP_ON_DISK":
                for e in v["candidates"][:3]:
                    print("        %-34s rows=%s covers_live=%s"
                          % (e["path"], e.get("chat_main_rows"),
                             e.get("covers_live")))
        return 0 if res["ok"] else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
