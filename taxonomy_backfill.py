# -*- coding: utf-8 -*-
"""
taxonomy_backfill.py — fill the thin ontology registry from GROUND TRUTH.

Measured problem (agent.db, active rows):
    channel 1 | module 3 | capability 14 | api 3 | function 1 | db_table 1 | db_field 6
while the contract SSOT next door holds 17 templates / 98 fields / 97 tdd cases.
`function_registry` has ONE row. So `function/*` and `api/*` scope resolution is
close to useless, and hardcode_scope correctly reports UNKNOWN.

This module PROPOSES registry rows and only writes the ones it can VERIFY.

Two rules, both load-bearing:

  R1  A proposal is only writable if OUR OWN check confirmed it. The citation is
      produced by the verifying command (`PRAGMA table_info(...)`, a real file
      path, a real route literal in a real file) -- never by a model.

  R2  The local 7B LLM (qwen2.5:7b-instruct, 24/7) may PROPOSE rows. Its output
      is a hypothesis, not evidence. A proposal whose verification fails is
      DISCARDED at the write site -- it is not downgraded to a low-confidence
      insert. This is the same rule as `citation_discipline`: no citation, no
      finding.

Sources, strongest first:
    1. the LIVE agent.db schema      (PRAGMA)          -> db_table, db_field
    2. real flask routes in *.py     (route literal)   -> api
    3. code_registry rows            (module/function) -> module, function
    4. skill_contract_template.taxonomy_path           -> seeds all levels
    5. the 7B LLM                    (proposal only)   -> module/capability grouping

Read-only unless `apply(..., dry_run=False)`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"
OLLAMA_BASE = "http://127.0.0.1:18803"
TEXT_MODEL = "qwen2.5:7b-instruct"

ROUTE_RE = re.compile(
    r"""@\s*[\w.]+\s*\.\s*route\(\s*['"]([^'"]+)['"]""")
METHOD_RE = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""")
DEF_RE = re.compile(r"""^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(""")
SKIP_TABLES = ("sqlite_", "sqlite_stat")

LEVELS = ("channel", "module", "capability", "function", "api",
          "db_table", "db_field")


def log(msg: str) -> None:
    print("[taxonomy_backfill] %s" % msg, flush=True)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# 1. LIVE schema -> db_table / db_field   (strongest evidence available)
# ---------------------------------------------------------------------------


def scan_live_schema(conn: sqlite3.Connection,
                     *, include: list[str] | None = None) -> list[dict]:
    """Read the REAL tables and columns. Citation = the PRAGMA that proved it."""
    out: list[dict] = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    for r in rows:
        table = r["name"]
        if any(table.startswith(p) for p in SKIP_TABLES):
            continue
        if include and table not in include:
            continue
        cols = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
        if not cols:
            continue
        out.append({
            "level": "db_table",
            "key": table,
            "name": table,
            "fields": [c["name"] for c in cols],
            "cite_ref": "PRAGMA table_info(%s) on agent.db" % table,
            "source": "live_schema",
            "verified": True,
            "verify_note": "%d column(s) read live" % len(cols),
        })
        for c in cols:
            out.append({
                "level": "db_field",
                "key": c["name"],
                "name": c["name"],
                "table": table,
                "cite_ref": "PRAGMA table_info(%s) on agent.db" % table,
                "source": "live_schema",
                "verified": True,
                "verify_note": "column %s.%s declared %s"
                               % (table, c["name"], c["type"] or "?"),
            })
    return out


# ---------------------------------------------------------------------------
# 2. real routes -> api
# ---------------------------------------------------------------------------


def scan_routes(roots: list[Path] | None = None) -> list[dict]:
    """Find real flask routes. Citation = file:line holding the literal."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    files: list[Path] = []
    for root in roots or [BASE_DIR]:
        root = Path(root)
        if root.is_dir():
            files.extend(sorted(root.glob("*.py")))
        elif root.is_file():
            files.append(root)
    for p in files:
        if p.name.startswith("_diag") or p.name.startswith("_smoke"):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            m = ROUTE_RE.search(line)
            if not m:
                continue
            path = m.group(1)
            if not path.startswith("/"):
                continue
            window = "\n".join(lines[i - 1:min(len(lines), i + 8)])
            mm = METHOD_RE.search(window)
            methods = []
            if mm:
                methods = [x.strip().strip("'\"").upper()
                           for x in mm.group(1).split(",") if x.strip()]
            if not methods:
                methods = ["GET"]
            for meth in methods:
                key = "%s %s" % (meth, path)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "level": "api",
                    "key": key,
                    "method": meth,
                    "path": path,
                    "name": key,
                    "cite_ref": "%s:%d" % (p.name, i),
                    "source": "route_literal",
                    "verified": True,
                    "verify_note": "route decorator literal at %s:%d"
                                   % (p.name, i),
                })
    return out


# ---------------------------------------------------------------------------
# 3. code_registry -> module / function
# ---------------------------------------------------------------------------


def scan_code_registry(conn: sqlite3.Connection) -> list[dict]:
    """code_registry is the bridge from a function name to its real file."""
    if not _exists(conn, "code_registry"):
        return []
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(code_registry)")}
    if not {"module_name", "function_name"} <= cols:
        return []
    sel = "module_name, function_name"
    sel += ", file_path" if "file_path" in cols else ", '' AS file_path"
    sel += ", status" if "status" in cols else ", '' AS status"
    sel += ", register_id" if "register_id" in cols else ", rowid AS register_id"
    out: list[dict] = []
    for r in conn.execute("SELECT %s FROM code_registry" % sel):
        mod = (r["module_name"] or "").strip()
        fn = (r["function_name"] or "").strip()
        fp = (r["file_path"] or "").strip()
        cite = "code_registry register_id=%s" % r["register_id"]
        if mod:
            out.append({
                "level": "module", "key": mod, "name": mod,
                "dir_hint": fp, "cite_ref": cite, "source": "code_registry",
                "verified": None,  # verified later against real dirs/files
                "verify_note": "declared by code_registry row",
            })
        if fn:
            key = "%s.%s" % (mod, fn) if mod else fn
            out.append({
                "level": "function", "key": key, "name": fn,
                "module": mod, "file_path": fp, "cite_ref": cite,
                "source": "code_registry", "verified": None,
                "verify_note": "declared by code_registry row",
            })
    return out


# ---------------------------------------------------------------------------
# 4. contracts -> seeds
# ---------------------------------------------------------------------------


def scan_contracts(conn: sqlite3.Connection) -> list[dict]:
    if not _exists(conn, "skill_contract_template"):
        return []
    cols = {c["name"] for c in
            conn.execute("PRAGMA table_info(skill_contract_template)")}
    if "taxonomy_path" not in cols:
        return []
    out: list[dict] = []
    q = ("SELECT contract_id, taxonomy_path FROM skill_contract_template "
         "WHERE taxonomy_path IS NOT NULL AND taxonomy_path <> ''")
    for r in conn.execute(q):
        tp = (r["taxonomy_path"] or "").strip()
        if "/" not in tp:
            continue
        head, _, tail = tp.partition("/")
        out.append({
            "level": head.strip().lower(), "key": tail.strip(), "name": tail.strip(),
            "cite_ref": "skill_contract_template.contract_id=%s" % r["contract_id"],
            "source": "contract_taxonomy",
            "verified": None,
            "verify_note": "taxonomy_path declared on a contract",
        })
    return out


# ---------------------------------------------------------------------------
# verification — the ONLY thing that can create a citation
# ---------------------------------------------------------------------------


_SOURCE_ROOTS = ("src",)
_VENDOR_MARKERS = ("node_modules", "site-packages", "dist-packages", ".venv",
                   "chrome_cdp_profile", "__pycache__", ".git", ".pytest_cache",
                   ".vite", ".claude", ".github", ".bin")
_VERSIONY = re.compile(r"^(?:\d+(?:\.\d+)*|[0-9a-fA-F]{12,}|_+)+$")


def _real_dirs() -> set[str]:
    """Real source DIRECTORY names, shallowly.

    A full rglob of the repo yields ~800 entries, most of them vendor/version
    dirs (`.bin`, `1.0.0.6_0`, extension folders). Feeding that to a verifier
    would let a junk name "prove" a module, and feeding it to the LLM makes the
    question meaningless (measured: 796 candidates -> the model answered nothing).
    So: top level, one level under each source root, and never a vendor or
    version-looking name.
    """
    dirs: set[str] = set()

    def _ok(name: str) -> bool:
        if not name or name.startswith("."):
            return False
        if name in _VENDOR_MARKERS:
            return False
        return not _VERSIONY.match(name)

    for child in BASE_DIR.iterdir():
        if child.is_dir() and _ok(child.name):
            dirs.add(child.name)
    for root in _SOURCE_ROOTS:
        base = BASE_DIR / root
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and _ok(child.name):
                dirs.add(child.name)
    return dirs


def _rel_candidates(fp: str) -> list[str]:
    s = (fp or "").replace("\\", "/").strip()
    if not s:
        return []
    return [s, s.split("/")[-1]]


def verify(prop: dict, *, conn: sqlite3.Connection,
           dirs: set[str] | None = None) -> dict:
    """Try to PROVE a proposal. Mutates and returns it with verified/verify_note.

    A proposal that cannot be proven keeps verified=False and is discarded at
    the write site. It is never inserted as a guess.
    """
    lvl = prop.get("level")
    dirs = dirs if dirs is not None else _real_dirs()

    if lvl == "db_table":
        ok = _exists(conn, prop["key"])
        prop["verified"] = ok
        prop["verify_note"] = ("table exists in agent.db" if ok
                              else "no such table in agent.db")
        return prop

    if lvl == "db_field":
        table = prop.get("table")
        if not table or not _exists(conn, table):
            prop["verified"] = False
            prop["verify_note"] = "parent table %r absent" % table
            return prop
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(%s)" % table)}
        ok = prop["key"] in cols
        prop["verified"] = ok
        prop["verify_note"] = ("column present on %s" % table if ok
                              else "column absent on %s" % table)
        prop["cite_ref"] = "PRAGMA table_info(%s) on agent.db" % table
        return prop

    if lvl == "api":
        prop["verified"] = True  # scan_routes only emits real literals
        return prop

    if lvl == "module":
        alias = prop.get("alias_for")
        if prop.get("kind") == "association" and alias:
            # An ASSOCIATION says "real dir X belongs to the EXISTING module M".
            # It is provable from two checkable facts, and it can never create a
            # row -- M is already registered. It only records a dir_hint, so
            # apply() must not treat it as an insert.
            real_alias = alias in dirs
            real_module = conn.execute(
                "SELECT 1 FROM module_registry WHERE module_key = ? "
                "AND is_active = 1", (prop["key"],)).fetchone() is not None \
                if _exists(conn, "module_registry") else False
            prop["verified"] = bool(real_alias and real_module)
            prop["creates_row"] = False
            prop["confidence"] = "derived"
            if prop["verified"]:
                prop["cite_ref"] = "filesystem: dir %s exists + module_registry(%s)" \
                    % (alias, prop["key"])
                prop["verify_note"] = ("dir %r is real and module %r is registered "
                                       "(association recorded, no new row)"
                                       % (alias, prop["key"]))
            else:
                prop["verify_note"] = (
                    "dir %r real=%s / module %r registered=%s"
                    % (alias, real_alias, prop["key"], real_module))
            return prop
        ok = prop["key"] in dirs
        if not ok:
            for cand in _rel_candidates(prop.get("dir_hint", "")):
                segs = [s for s in cand.split("/") if s]
                if segs and segs[0] in dirs:
                    ok = True
                    prop["verify_note"] = "file path segment %r is a real dir" % segs[0]
                    prop["cite_ref"] = "filesystem: dir %s exists" % segs[0]
                    break
        prop["verified"] = ok
        prop["creates_row"] = True
        if ok and not prop.get("cite_ref"):
            prop["cite_ref"] = "filesystem: dir %s exists" % prop["key"]
        if not ok:
            prop["verify_note"] = ("no directory named %r and no file path "
                                   "segment matches" % prop["key"])
        return prop

    if lvl == "function":
        ok = False
        note = "no file_path to prove"
        for cand in _rel_candidates(prop.get("file_path", "")):
            for base in (BASE_DIR / cand, BASE_DIR / "src" / cand):
                if base.is_file():
                    ok = True
                    note = "file exists: %s" % base.relative_to(BASE_DIR)
                    prop["cite_ref"] = "filesystem: %s exists" % \
                        base.relative_to(BASE_DIR).as_posix()
                    break
            if ok:
                break
        if not ok:
            # a function may be proven by its real def name in a real file
            name = prop.get("name") or prop["key"].split(".")[-1]
            for p in list(BASE_DIR.glob("*.py")) + list((BASE_DIR / "src").rglob("*.py")):
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                ln = next((i for i, l in enumerate(txt.splitlines(), 1)
                           if DEF_RE.match(l) and DEF_RE.match(l).group(1) == name),
                          None)
                if ln:
                    ok = True
                    note = "def %s found at %s:%d" % (name, p.name, ln)
                    prop["cite_ref"] = "%s:%d" % (p.name, ln)
                    break
        prop["verified"] = ok
        prop["verify_note"] = note
        return prop

    if lvl == "capability":
        # A capability is a DESIGN AGGREGATE, not something code can prove.
        # Accepting "a function_registry row mentions it" would be circular --
        # those function rows may themselves have just been backfilled by us.
        # The only honest evidence is a DECLARATION: a contract whose
        # taxonomy_path names this capability.
        key = prop["key"]
        cite = prop.get("cite_ref")
        declared = (prop.get("source") == "contract_taxonomy" and cite)
        prop["verified"] = bool(declared)
        prop["verify_note"] = (
            "declared by %s" % cite if declared
            else "no declaration proves it (code cannot prove a capability)")
        return prop

    if lvl == "channel":
        prop["verified"] = False
        prop["verify_note"] = "channel is not backfilled (single channel exists)"
        return prop

    prop["verified"] = False
    prop["verify_note"] = "unknown level %r" % lvl
    return prop


# ---------------------------------------------------------------------------
# 5. the 7B LLM — proposal only, never evidence
# ---------------------------------------------------------------------------


def llm_propose_modules(*, missing: list[str], known: list[str],
                        timeout: float = 180.0) -> list[dict]:
    """Ask the local text model to group unknown names onto known modules.

    Output is a HYPOTHESIS. `verify()` decides whether any of it is real; the
    caller DISCARDS what cannot be proven. If the model is down we return [] --
    a dead model must never become a silent 'confidently nothing'.
    """
    prompt = (
        "You are helping fill a software ontology registry.\n"
        "Known module keys: %s\n"
        "Unknown names observed in code: %s\n\n"
        "For each unknown name, say which known module it most likely belongs "
        "to, or \"none\".\n"
        "Reply with JSON only, shaped: {\"map\": {\"<unknown>\": \"<module key>\"}}"
        % (json.dumps(known), json.dumps(missing))
    )
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "stream": False,
    }
    req = urllib.request.Request(
        OLLAMA_BASE.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        raw = body["choices"][0]["message"]["content"]
    except Exception as e:
        log("llm unavailable (%s); returning no proposals" % type(e).__name__)
        return []
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        log("llm reply had no JSON object; discarding all %d proposal(s)"
            % len(missing))
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        log("llm JSON unparseable; discarding all proposals")
        return []
    out: list[dict] = []
    for unk, mod in (data.get("map") or {}).items():
        if not mod or str(mod).lower() == "none":
            continue
        out.append({
            "level": "module", "key": str(mod).strip(), "name": str(mod).strip(),
            "kind": "association", "creates_row": False,
            "alias_for": unk,
            "cite_ref": None,          # a model supplies no citation
            "source": "llm_proposal",
            "verified": None,
            "verify_note": "proposed by %s; awaiting verification" % TEXT_MODEL,
        })
    return out


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def propose(conn: sqlite3.Connection, *, use_llm: bool = False,
            schema_include: list[str] | None = None) -> dict[str, Any]:
    """Collect proposals from every source and VERIFY each one."""
    proposals: list[dict] = []
    proposals += scan_live_schema(conn, include=schema_include)
    proposals += scan_routes()
    proposals += scan_code_registry(conn)
    proposals += scan_contracts(conn)

    dirs = _real_dirs()
    if use_llm and _exists(conn, "module_registry"):
        known = [r["module_key"] for r in conn.execute(
            "SELECT module_key FROM module_registry WHERE is_active = 1")]
        seen_mods = {p["key"] for p in proposals if p["level"] == "module"}
        missing = sorted(m for m in dirs if m not in known and m not in seen_mods)
        proposals += llm_propose_modules(missing=missing[:40], known=known)

    verified: list[dict] = []
    rejected: list[dict] = []
    for p in proposals:
        verify(p, conn=conn, dirs=dirs)
        (verified if p.get("verified") else rejected).append(p)

    by_level: dict[str, int] = {}
    for p in verified:
        by_level[p["level"]] = by_level.get(p["level"], 0) + 1
    associations = [p for p in verified
                    if p.get("creates_row") is False]
    return {
        "ok": True,
        "proposed": len(proposals),
        "verified": len(verified),
        "rejected": len(rejected),
        "insertable": len([p for p in verified if p.get("creates_row", True)]),
        "associations": len(associations),
        "by_level": by_level,
        "verified_rows": verified,
        "rejected_rows": rejected,
    }


_INSERT_SQL: dict[str, tuple[str, str, str | None]] = {
    "channel": ("channel_registry", "channel_key", None),
    "module": ("module_registry", "module_key", "channel_id"),
    "capability": ("capability_registry", "capability_key", "module_id"),
    "api": ("api_registry", "api_key", "capability_id"),
    "function": ("function_registry", "function_key", "capability_id"),
    "db_table": ("db_table_registry", "table_key", None),
}

# Extra columns to write per level, beyond (key, name). `function_registry.file_path`
# is the ONE reverse-lookup handle `hardcode_scope` has for (file -> taxon), so a
# function row without it cannot be found again. Measured before this change:
# `function_registry` had 1 row and its file_path was the only one set.
_EXTRA_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "api": (("method", "method"), ("path", "path")),
    "function": (("file_path", "file_path"),),
}


def _resolve_parent_id(conn: sqlite3.Connection, level: str,
                       prop: dict) -> tuple[int | None, str]:
    """Find the NOT NULL parent id a new row needs. Returns (id, source_note).

    A missing parent is NOT filled with a plausible guess. If the parent cannot
    be identified, the row is skipped -- a registry row with a made-up parent is
    worse than no row, because it makes the FK graph lie.
    """
    fk = _INSERT_SQL[level][2]
    if not fk:
        return None, "no parent required"

    if level == "module":            # parent = channel
        rows = conn.execute(
            "SELECT channel_id, channel_key FROM channel_registry "
            "WHERE is_active = 1").fetchall() if _exists(conn, "channel_registry") else []
        if len(rows) == 1:
            return rows[0]["channel_id"], "sole active channel %r" % rows[0]["channel_key"]
        return None, ("%d active channel(s): parent ambiguous" % len(rows))

    if level in ("api", "function"):  # parent = capability, DECLARED not derived
        # Code cannot prove this parent. Measured: a route decorator proves the
        # route exists and the file proves the MODULE, but nothing says WHICH
        # capability the route serves -- and no module has exactly one
        # capability (mouse_spot_helper has 10, task_center has 4), so it can
        # never be derived uniquely.
        #
        # An earlier revision tried to derive it through `hardcode_scope` and
        # passed `text=""`, which made the route lookup fail by construction. Had
        # it succeeded it would have attached every api row of one hub file to a
        # single capability -- a fake success, and the exact defect class this
        # project keeps repairing. So the parent comes from a CONFIRMED
        # declaration or the row is skipped.
        try:
            import capability_binding as cb
        except Exception:
            return None, "capability_binding unavailable"

        route = prop.get("path") or None
        fp = (prop.get("file_path") or "").strip()
        if not fp:
            # derive the file from the citation (`path:line`)
            ref = prop.get("cite_ref") or ""
            cand = ref.split(":")[0]
            if cand.endswith(".py"):
                fp = cand
        res = cb.resolve_parent(conn, file_path=fp or None, route=route)
        if not res.get("ok"):
            return None, ("no CONFIRMED capability binding (%s)"
                          % res.get("why"))
        return res["capability_id"], ("declared capability %r via %s (%s)"
                                      % (res["capability_key"], res["via"],
                                         res["cite_ref"]))

    if level == "capability":         # parent = module
        return None, "no proven module parent"

    return None, "no parent rule for level %s" % level


def apply(conn: sqlite3.Connection, result: dict, *, dry_run: bool = True,
          levels: list[str] | None = None) -> dict[str, Any]:
    """Insert only VERIFIED rows. An unverified row is DISCARDED, not inserted.

    db_field is intentionally not auto-inserted here: it needs a parent
    db_table_id and the column list is large. It is reported so a human can
    approve the bulk insert separately.
    """
    inserted: list[dict] = []
    skipped: list[dict] = []
    want = set(levels or ("module", "api", "db_table"))
    for p in result.get("verified_rows", []):
        lvl = p["level"]
        if lvl not in want:
            continue
        if p.get("creates_row") is False:
            skipped.append({**p, "why": "association: records a dir_hint, "
                                         "creates no row"})
            continue
        spec = _INSERT_SQL.get(lvl)
        if not spec:
            skipped.append({**p, "why": "no insert path for level %s" % lvl})
            continue
        table, key_col, _fk = spec
        if not _exists(conn, table):
            skipped.append({**p, "why": "table %s absent" % table})
            continue
        row = conn.execute(
            "SELECT 1 FROM %s WHERE %s = ?" % (table, key_col),
            (p["key"],)).fetchone()
        if row:
            skipped.append({**p, "why": "already registered"})
            continue
        # a row without a cite_ref is not allowed to be written, ever
        if not p.get("cite_ref"):
            skipped.append({**p, "why": "NO CITATION -> discarded"})
            continue
        # The parent is resolved BEFORE the dry-run branch, because a dry run that
        # skips the parent check reports an insert the real run cannot make.
        # Measured: this ordering made a dry run claim "count: 214" while ZERO
        # capability bindings were confirmed -- a plan that looks successful and
        # is impossible, which is worse than no plan.
        fk = spec[2]
        parent_id = None
        if fk:
            parent_id, why = _resolve_parent_id(conn, lvl, p)
            if parent_id is None:
                skipped.append({**p, "why": "no provable parent -> skipped (%s)"
                                            % why})
                continue
        if dry_run:
            inserted.append({**p, "dry_run": True,
                             "parent_id": parent_id, "parent_note": why
                             if fk else "no parent required"})
            continue
        cols = [key_col, "name"]
        vals = [p["key"], p.get("name") or p["key"]]
        if fk:
            cols.append(fk)
            vals.append(parent_id)
        if lvl == "api":
            for extra, val in (("method", p.get("method")),
                               ("path", p.get("path"))):
                if val:
                    cols.append(extra)
                    vals.append(val)
        # extra columns (api method/path, function file_path)
        for col, key in _EXTRA_COLUMNS.get(lvl, ()):
            val = p.get(key)
            if not val and key == "file_path":
                # derive it from the citation; without it the row cannot be
                # found again by (file -> taxon)
                ref = (p.get("cite_ref") or "").split(":")[0]
                if ref.endswith(".py"):
                    val = ref
            if val:
                cols.append(col)
                vals.append(val)
        conn.execute(
            "INSERT INTO %s (%s) VALUES (%s)"
            % (table, ", ".join(cols), ", ".join("?" * len(cols))),
            vals)
        inserted.append(p)
    if not dry_run:
        conn.commit()
    return {"ok": True, "dry_run": dry_run,
            "would_insert" if dry_run else "inserted": len(inserted),
            "skipped": len(skipped), "rows": inserted, "skipped_rows": skipped}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="really insert (default is dry-run)")
    ap.add_argument("--llm", action="store_true", help="allow 7B proposals")
    ap.add_argument("--levels", default="module,api,db_table")
    args = ap.parse_args()

    conn = _connect()
    try:
        res = propose(conn, use_llm=args.llm)
        print("proposed=%d verified=%d rejected=%d" %
              (res["proposed"], res["verified"], res["rejected"]))
        print("verified by level:", json.dumps(res["by_level"], sort_keys=True))
        out = apply(conn, res, dry_run=not args.apply,
                    levels=[s.strip() for s in args.levels.split(",") if s.strip()])
        print("insert:", json.dumps(
            {"dry_run": out["dry_run"],
             "count": out.get("would_insert", out.get("inserted")),
             "skipped": out["skipped"]}, ensure_ascii=False))
        for s in out["skipped_rows"][:8]:
            print("   skipped %-9s %-34s %s"
                  % (s["level"], s["key"], s["why"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# object_door: kind-agnostic by definition (no DDL in this file)
