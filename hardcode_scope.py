# -*- coding: utf-8 -*-
"""
hardcode_scope.py — resolve WHICH taxon a hard-coded literal sits in.

Range (user-declared, narrowest -> widest):
    db_field > db_table > function > api > capability > module > channel

Given (file_path, line) this module answers ONE question:
    "what is the narrowest scope, from the declared range, that this code lives in?"

It NEVER guesses. Every resolved level carries a `source` that says HOW it was
resolved, and a `confidence` that says how strong that evidence is:

    source            confidence   meaning
    ---------------------------------------------------------------------
    registry_fk       exact        a real foreign-key chain in the registry
    line_literal      exact        the exact registered key appears on the line
    path_segment      derived      a directory segment equals a registered key
    (none)            --           never used; a miss is reported, not filled

A level that is not resolvable is NOT filled in. If nothing resolves at all the
result is one of two DISTINCT unknowns (they are not the same problem):

    UNKNOWN_NO_REGISTRY   the level's registry is empty/thin, so the question
                          could not even be asked. Normal flow continues, but
                          the form must name the missing level.
    UNKNOWN_NOT_FOUND     the registry HAS rows and none matched. This may only
                          ESCALATE -- an unresolvable scope cannot be judged.

Why this distinction matters: `function_registry` and `api_registry` are nearly
empty today (measured 1 row / 3 rows). If every UNKNOWN could only escalate,
every hard-coded literal in the repo would be escalated and the review forum
would be flooded. The two kinds are kept apart so that "we lack the data" is
not confused with "we looked and failed".

Read-only. Nothing here writes to any registry.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# the declared range
# ---------------------------------------------------------------------------

# narrowest -> widest. This ORDER is load-bearing: `resolve()` returns the
# FIRST entry that resolves, so the list is the answer to "which one wins".
SCOPE_ORDER: tuple[str, ...] = (
    "db_field",
    "db_table",
    "function",
    "api",
    "capability",
    "module",
    "channel",
)

SCOPE_RANK: dict[str, int] = {lvl: i for i, lvl in enumerate(SCOPE_ORDER)}

# registry table + key column + the FK parent, per level
_REGISTRY: dict[str, tuple[str, str, str | None]] = {
    "channel": ("channel_registry", "channel_key", None),
    "module": ("module_registry", "module_key", "channel_registry"),
    "capability": ("capability_registry", "capability_key", "module_registry"),
    "function": ("function_registry", "function_key", "capability_registry"),
    "api": ("api_registry", "api_key", "capability_registry"),
    "db_table": ("db_table_registry", "table_key", None),
    "db_field": ("db_field_registry", "field_key", "db_table_registry"),
}

# FK column name per level's parent
_PARENT_FK: dict[str, str] = {
    "module": "channel_id",
    "capability": "module_id",
    "function": "capability_id",
    "api": "capability_id",
    "db_field": "db_table_id",
}

# PRIMARY KEY column per level. FK lookups join on the PK, never on the
# human-readable key column (`table_key` is not `db_table_id`).
_PK: dict[str, str] = {
    "channel": "channel_id",
    "module": "module_id",
    "capability": "capability_id",
    "function": "function_id",
    "api": "api_id",
    "db_table": "db_table_id",
    "db_field": "db_field_id",
}

UNKNOWN = "UNKNOWN"
UNKNOWN_NO_REGISTRY = "UNKNOWN_NO_REGISTRY"
UNKNOWN_NOT_FOUND = "UNKNOWN_NOT_FOUND"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ROUTE = re.compile(r"""@\s*\w+\s*\.\s*route\(\s*['"]([^'"]+)['"]""")
_STRING_SPAN = re.compile("'([^']*)'|\"([^\"]*)\"")
_BAD_FIELD_KEYS = frozenset({"none", "n/a", "na", "-", ""})

_DEFAULT_DB = BASE_DIR / "agent.db"


def log(msg: str) -> None:
    print("[hardcode_scope] %s" % msg, flush=True)


def _connect(db_path: Path | str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or _DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# read the code being judged
# ---------------------------------------------------------------------------


def _norm_path(p: Path | str) -> str:
    """Repo-relative, forward slashes, lowercased drive for comparison."""
    try:
        rel = Path(p).resolve().relative_to(BASE_DIR)
        s = str(rel).replace("\\", "/")
    except Exception:
        s = str(p).replace("\\", "/")
    return s


def _read_lines(file_path: Path | str) -> list[str] | None:
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return text.splitlines()


def _window(lines: list[str] | None, line: int, before: int = 3,
            after: int = 3) -> str:
    """The code around `line`, which is what a route decorator needs."""
    if not lines or line is None:
        return ""
    idx = max(0, int(line) - 1)
    lo = max(0, idx - before)
    hi = min(len(lines), idx + after + 1)
    return "\n".join(lines[lo:hi])


def _tokens(text: str) -> list[str]:
    seen: list[str] = []
    for m in _IDENT.finditer(text or ""):
        t = m.group(0)
        if t not in seen:
            seen.append(t)
    return seen


def _string_spans(text: str) -> str:
    """The contents of every string literal, joined by a sentinel.

    A table/field name is HARD-CODED as a string in SQL and in mapping dicts.
    Matching bare identifiers instead is measuring the wrong thing: after the
    registry was backfilled to 111 tables, one of them is named `app`, and
    `app.route(...)` -- the most ordinary Flask line there is -- started reading
    as `db_table=app` and beat the correct `api` verdict by being narrower.
    A sentinel (not a space) keeps `'a' 'b'` from merging into a token `a b`.
    """
    out = []
    for m in _STRING_SPAN.finditer(text or ""):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        if s:
            out.append(s)
    return " \x00 ".join(out)


def _sql_identifiers(text: str) -> set[str]:
    """Identifiers written in the code (for SQL-style `col = ?` matching)."""
    return set(_tokens(text))


# ---------------------------------------------------------------------------
# registry lookups
# ---------------------------------------------------------------------------


def _active_keys(conn: sqlite3.Connection, level: str) -> list[str]:
    table, key_col, _ = _REGISTRY[level]
    if not _table_exists(conn, table):
        return []
    rows = conn.execute(
        "SELECT %s AS k FROM %s WHERE is_active = 1" % (key_col, table)
    ).fetchall()
    return [r["k"] for r in rows if r["k"] is not None]


def _get(conn: sqlite3.Connection, level: str, key: str) -> dict | None:
    table, key_col, _ = _REGISTRY[level]
    if not _table_exists(conn, table):
        return None
    row = conn.execute(
        "SELECT * FROM %s WHERE %s = ? AND is_active = 1" % (table, key_col),
        (key,),
    ).fetchone()
    return dict(row) if row else None


def _widen(conn: sqlite3.Connection, level: str, key: str) -> list[dict]:
    """Walk the FK chain upward from a resolved level, returning the chain.

    Each entry: {level, key, source, confidence}. Stops as soon as a parent
    cannot be resolved -- it does NOT skip a level to reach a grandparent,
    because an unproven link is not a link.
    """
    chain: list[dict] = []
    cur_level, cur_key = level, key
    while True:
        parent_level = _parent_of(cur_level)
        fk = _PARENT_FK.get(cur_level)
        if not parent_level or not fk:
            break
        ptable, pkey_col, _ = _REGISTRY[parent_level]
        if not _table_exists(conn, ptable):
            break
        row = _get(conn, cur_level, cur_key)
        if not row or row.get(fk) is None:
            break
        # the FK points at the parent's PRIMARY KEY, not its key column
        prow = conn.execute(
            "SELECT * FROM %s WHERE %s = ? AND is_active = 1"
            % (ptable, _PK[parent_level]),
            (row[fk],),
        ).fetchone()
        if not prow:
            break
        pk = prow[pkey_col]
        chain.append({"level": parent_level, "key": pk,
                      "source": "registry_fk", "confidence": "exact"})
        cur_level, cur_key = parent_level, pk
    return chain


# explicit map registry-table-name -> level (a readable table beats a clever lookup)
_PARENT_LEVEL: dict[str, str] = {
    "channel_registry": "channel",
    "module_registry": "module",
    "capability_registry": "capability",
    "db_table_registry": "db_table",
}


def _parent_of(level: str) -> str | None:
    """Which level is the FK parent of `level` (None = top of the chain)."""
    entry = _REGISTRY.get(level)
    if not entry:
        return None
    return _PARENT_LEVEL.get(entry[2] or "")


# ---------------------------------------------------------------------------
# per-level resolution
# ---------------------------------------------------------------------------


def _resolve_db_field(conn, text: str, tried: list) -> dict | None:
    fields = []
    if _table_exists(conn, "db_field_registry"):
        rows = conn.execute(
            "SELECT f.field_key AS fk, t.table_key AS tk "
            "FROM db_field_registry f "
            "JOIN db_table_registry t ON t.db_table_id = f.db_table_id "
            "WHERE f.is_active = 1 AND t.is_active = 1"
        ).fetchall()
        fields = [dict(r) for r in rows]
    quoted = set(_tokens(_string_spans(text)))
    for f in fields:
        # the field and its table must BOTH appear inside string literals
        if f["fk"] in quoted and f["tk"] in quoted:
            tried.append({"level": "db_field", "hit": True,
                          "how": "line_literal (field and its table both appear "
                                 "inside string literals)"})
            return {"level": "db_field", "key": f["fk"], "table": f["tk"],
                    "source": "line_literal", "confidence": "exact"}
    tried.append({"level": "db_field", "hit": False,
                  "how": "line_literal", "considered": len(fields)})
    return None


def _resolve_db_table(conn, text: str, tried: list) -> dict | None:
    keys = _active_keys(conn, "db_table")
    # A table name is hard-coded as a STRING (in SQL, or a mapping key), so the
    # match is on string-literal contents. Matching bare identifiers would let
    # an ordinary variable name collide with a table name -- measured: a table
    # named `app` made every `app.route(...)` line read as db_table=app.
    quoted = set(_tokens(_string_spans(text)))
    for k in keys:
        if k in quoted:
            tried.append({"level": "db_table", "hit": True,
                          "how": "line_literal (table name inside a string)"})
            return {"level": "db_table", "key": k,
                    "source": "line_literal", "confidence": "exact"}
    tried.append({"level": "db_table", "hit": False,
                  "how": "line_literal", "considered": len(keys)})
    return None


def _resolve_function(conn, rel: str, tried: list) -> dict | None:
    if not _table_exists(conn, "function_registry"):
        tried.append({"level": "function", "hit": False,
                      "how": "file_path reverse lookup",
                      "note": "table absent"})
        return None
    rows = conn.execute(
        "SELECT function_key, file_path FROM function_registry "
        "WHERE is_active = 1 AND file_path IS NOT NULL AND file_path <> ''"
    ).fetchall()
    for r in rows:
        fp = _norm_path(r["file_path"])
        if fp and (fp == rel or rel.endswith(fp) or fp.endswith(rel)):
            tried.append({"level": "function", "hit": True,
                          "how": "file_path reverse lookup"})
            return {"level": "function", "key": r["function_key"],
                    "source": "registry_fk", "confidence": "exact"}
    tried.append({"level": "function", "hit": False,
                  "how": "file_path reverse lookup",
                  "considered": len(rows)})
    return None


def _resolve_api(conn, text: str, tried: list) -> dict | None:
    if not _table_exists(conn, "api_registry"):
        tried.append({"level": "api", "hit": False, "how": "route literal",
                      "note": "table absent"})
        return None
    rows = conn.execute(
        "SELECT api_key, method, path FROM api_registry WHERE is_active = 1"
    ).fetchall()
    routes = _ROUTE.findall(text or "")
    for r in rows:
        p = (r["path"] or "").strip()
        if not p or p.lower() in _BAD_FIELD_KEYS:
            continue
        if p in routes or ("'%s'" % p) in (text or "") \
                or ('"%s"' % p) in (text or ""):
            tried.append({"level": "api", "hit": True, "how": "route literal"})
            return {"level": "api", "key": r["api_key"],
                    "path": p, "method": r["method"],
                    "source": "line_literal", "confidence": "exact"}
    tried.append({"level": "api", "hit": False, "how": "route literal",
                  "considered": len(rows), "routes_on_line": routes})
    return None


def _resolve_capability(conn, text: str, tried: list) -> dict | None:
    keys = _active_keys(conn, "capability")
    toks = set(_tokens(text))
    for k in keys:
        if k in toks:
            tried.append({"level": "capability", "hit": True,
                          "how": "line_literal"})
            return {"level": "capability", "key": k,
                    "source": "line_literal", "confidence": "exact"}
    tried.append({"level": "capability", "hit": False,
                  "how": "line_literal", "considered": len(keys)})
    return None


def _resolve_module(conn, rel: str, tried: list) -> dict | None:
    keys = _active_keys(conn, "module")
    segs = [s for s in rel.split("/") if s]
    for k in keys:
        if k in segs:
            tried.append({"level": "module", "hit": True,
                          "how": "path_segment (%r is a directory segment)" % k})
            return {"level": "module", "key": k,
                    "source": "path_segment", "confidence": "derived"}
    # A file whose stem is EXACTLY a registered module key belongs to that
    # module. Measured need: `mouse_spot_helper` is a registered module and its
    # code lives in `mouse_spot_helper.py` at the repo ROOT, so no directory
    # segment matches and the whole root-file family resolved to nothing.
    # This is checkable evidence (string equality on the file name), the same
    # grade as a path segment -- not a guess at similarity.
    stem = segs[-1].rsplit(".", 1)[0] if segs else ""
    for k in keys:
        if stem == k:
            tried.append({"level": "module", "hit": True,
                          "how": "file_stem (%r.py is named after module %r)"
                                 % (k, k)})
            return {"level": "module", "key": k,
                    "source": "file_stem", "confidence": "derived"}
    tried.append({"level": "module", "hit": False,
                  "how": "path_segment / file_stem", "considered": len(keys)})
    return None


def _resolve_channel(conn, found: dict, tried: list) -> dict | None:
    """Channel is resolved ONLY by a proven FK path, never by 'there is one'.

    A sole active channel is a tautology, not evidence. If it were accepted,
    EVERY unresolved literal would fall back to channel level and the whole
    tribunal would classify as a channel-wide system design problem -- the
    exact flood the two-unknown design exists to prevent.
    """
    if not _table_exists(conn, "channel_registry"):
        tried.append({"level": "channel", "hit": False, "how": "FK path",
                      "note": "table absent"})
        return None
    # _widen already records any channel it reached; a miss means no proven path
    tried.append({"level": "channel", "hit": False, "how": "FK path",
                  "note": "no foreign-key path proved a channel (a sole channel "
                          "is a tautology, not evidence)",
                  "considered": len(_active_keys(conn, "channel"))})
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def resolve(
    file_path: Path | str,
    line: int | None = None,
    *,
    db_path: Path | str | None = None,
    text: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Resolve the narrowest scope for (file_path, line).

    Returns a dict; never raises for a miss (a miss is a value, not an error).
    `text` overrides the code read from disk (used by the proof harness).
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    rel = _norm_path(file_path)
    lines = None if text is not None else _read_lines(file_path)
    win = text if text is not None else _window(lines, line or 1)
    tried: list[dict] = []

    try:
        if not _table_exists(conn, "channel_registry"):
            return _unknown(rel, line, tried, kind=UNKNOWN_NO_REGISTRY,
                            missing=[lvl for lvl in SCOPE_ORDER],
                            note="ontology registry absent in this DB")

        found: dict[str, dict] = {}
        for lvl, fn in (
            ("db_field", lambda: _resolve_db_field(conn, win, tried)),
            ("db_table", lambda: _resolve_db_table(conn, win, tried)),
            ("function", lambda: _resolve_function(conn, rel, tried)),
            ("api", lambda: _resolve_api(conn, win, tried)),
            ("capability", lambda: _resolve_capability(conn, win, tried)),
            ("module", lambda: _resolve_module(conn, rel, tried)),
        ):
            hit = fn()
            if hit:
                found[lvl] = hit

        # widen by FK from the narrowest hit
        for lvl in SCOPE_ORDER:
            if lvl in found:
                for w in _widen(conn, lvl, found[lvl]["key"]):
                    found.setdefault(w["level"], w)
                break

        if "channel" not in found:
            ch = _resolve_channel(conn, found, tried)
            if ch:
                found["channel"] = ch

        if not found:
            return _unknown(rel, line, tried,
                            **_classify_miss(conn))

        narrowest = min(found, key=lambda l: SCOPE_RANK[l])
        chain = [found[l] for l in SCOPE_ORDER if l in found]
        return {
            "ok": True,
            "file": rel,
            "line": line,
            "scope_level": narrowest,
            "scope_ref": found[narrowest]["key"],
            "confidence": found[narrowest]["confidence"],
            "source": found[narrowest]["source"],
            "chain": chain,
            "tried": tried,
            "unknown_kind": None,
            "missing_levels": [],
        }
    finally:
        if own:
            conn.close()


# The four PRECISE levels. "Can we classify at all?" is a question about these:
# db_field / db_table are schema ground truth; function / api are where code
# lives. module / capability / channel are aggregates that only get you a
# bigger forum, so their emptiness does not make the question unaskable.
_PRECISE_LEVELS: tuple[str, ...] = ("db_field", "db_table", "function", "api")


def _classify_miss(conn: sqlite3.Connection) -> dict[str, Any]:
    """Decide WHICH unknown this is: 'no instruments' vs 'instruments, no hit'."""
    missing = [lvl for lvl in SCOPE_ORDER
               if not _table_exists(conn, _REGISTRY[lvl][0])
               or not _active_keys(conn, lvl)]
    precise_filled = [lvl for lvl in _PRECISE_LEVELS
                      if _table_exists(conn, _REGISTRY[lvl][0])
                      and _active_keys(conn, lvl)]
    if not precise_filled:
        return {"kind": UNKNOWN_NO_REGISTRY, "missing": missing,
                "note": "none of the precise levels (%s) has a single row, so "
                        "the scope question could not be asked"
                        % ", ".join(_PRECISE_LEVELS)}
    return {"kind": UNKNOWN_NOT_FOUND, "missing": missing,
            "note": "%d precise level(s) hold data (%s) and none matched this "
                    "file/line" % (len(precise_filled), ", ".join(precise_filled))}


def _unknown(rel, line, tried, *, kind, missing, note) -> dict[str, Any]:
    return {
        "ok": False,
        "file": rel,
        "line": line,
        "scope_level": kind,
        "scope_ref": "",
        "confidence": None,
        "source": None,
        "chain": [],
        "tried": tried,
        "unknown_kind": kind,
        "missing_levels": missing,
        "note": note,
    }


def is_unknown(result: dict) -> bool:
    return not result.get("ok")


def may_only_escalate(result: dict) -> bool:
    """UNKNOWN_NOT_FOUND may only escalate. UNKNOWN_NO_REGISTRY may not.

    `UNKNOWN_NO_REGISTRY` means the question could not be asked (thin data) --
    blocking it would block everything. `UNKNOWN_NOT_FOUND` means we looked and
    failed, so necessity cannot be judged at all.
    """
    return result.get("unknown_kind") == UNKNOWN_NOT_FOUND


def narrowest_of(levels: list[str]) -> str | None:
    known = [l for l in levels if l in SCOPE_RANK]
    return min(known, key=lambda l: SCOPE_RANK[l]) if known else None


def widest_of(levels: list[str]) -> str | None:
    known = [l for l in levels if l in SCOPE_RANK]
    return max(known, key=lambda l: SCOPE_RANK[l]) if known else None


def registry_fill(db_path: Path | str | None = None) -> dict[str, Any]:
    """How full is each registry? Thin registries are the root of UNKNOWN."""
    conn = _connect(db_path)
    try:
        out: dict[str, Any] = {}
        for lvl in SCOPE_ORDER:
            table, key_col, _ = _REGISTRY[lvl]
            if not _table_exists(conn, table):
                out[lvl] = {"table": table, "exists": False, "active": 0,
                            "keys": []}
                continue
            keys = _active_keys(conn, lvl)
            out[lvl] = {"table": table, "exists": True, "active": len(keys),
                        "keys": keys}
        return out
    finally:
        conn.close()


def explain(result: dict) -> str:
    """Human-readable one-screen explanation of a resolve() result."""
    out = ["scope for %s:%s" % (result.get("file"), result.get("line"))]
    if result.get("ok"):
        out.append("  => %s  %s   (%s / %s)"
                   % (result["scope_level"], result["scope_ref"],
                      result["source"], result["confidence"]))
        out.append("  chain (narrow -> wide):")
        for c in result["chain"]:
            out.append("    %-11s %-34s %s" % (c["level"], c["key"], c["source"]))
    else:
        out.append("  => %s" % result["scope_level"])
        out.append("  note: %s" % result.get("note"))
        if result.get("missing_levels"):
            out.append("  empty registries: %s"
                       % ", ".join(result["missing_levels"]))
    out.append("  levels tried:")
    for t in result.get("tried", []):
        extra = t.get("how", "")
        if "considered" in t:
            extra += " [%d candidate(s)]" % t["considered"]
        if t.get("note"):
            extra += " -- %s" % t["note"]
        out.append("    %-11s %-5s %s" % (t["level"],
                                          "HIT" if t.get("hit") else "miss",
                                          extra))
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    fill = registry_fill()
    print("== registry fill (agent.db) ==")
    for lvl, info in fill.items():
        print("  %-11s exists=%-5s active=%d"
              % (lvl, info["exists"], info["active"]))
    print()
    if len(sys.argv) > 1:
        target = sys.argv[1]
        ln = int(sys.argv[2]) if len(sys.argv) > 2 else None
        print(explain(resolve(target, ln)))
        print()
    print(json.dumps(fill, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()

# object_door: kind-agnostic by definition (no DDL in this file)
