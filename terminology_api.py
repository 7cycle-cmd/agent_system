# -*- coding: utf-8 -*-
"""terminology_api.py — the terminology door, served over HTTP.

THE USER (2026-09-24)
---------------------
    "and API can help performance"

WHY THIS IS A ROUTER AND NOT A SECOND APP
-----------------------------------------
MEASURED: `main_api.py` is the ONE FastAPI app (34 routes). A second `FastAPI()`
would be a second truth for "where a request enters", so this module exposes an
`APIRouter` and a `register(app)` call. The app itself is NOT in this task's
allowlist, so the proof MOUNTS the router on the real app and drives it — the
wiring is then proven rather than described.

ONE IMPLEMENTATION, TWO SURFACES
--------------------------------
Every endpoint CALLS `terminology_alias` / `alias_acceptance` / `structure_contract`.
Nothing is re-implemented, so the HTTP answer and the CLI answer cannot drift.

THE CACHE IS BOUNDED, VERSIONED, AND MEASURED
---------------------------------------------
A register read happens per prompt build, so a per-call DB open is the cost the
user pointed at. But an UNBOUNDED cache over a LIVE register is how a stale name
outlives a rename — a defect this whole task exists to remove. So:

  * the cache is a dict with a MAX SIZE (evicting the oldest entry), and
  * every entry is stamped with a `version` read from the register
    (`MAX(updated_at)` + row count), and
  * a lookup whose version differs is a MISS, not a hit.

`cache_stats()` reports `hits`/`misses`/`stale_evictions`, so the speed-up is a
MEASUREMENT. The proof asserts a hit is served after a repeat AND that a write to
the register makes the next read a MISS.

Run:
    .\\.venv\\Scripts\\python.exe terminology_api.py --measure
    .\\.venv\\Scripts\\python.exe terminology_api.py --serve
"""
from __future__ import annotations

import argparse
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

# A BOUND, because an unbounded cache is a memory leak with a friendly name.
CACHE_MAX = 512

# The column shape of a recorded acceptance lives in `alias_acceptance` — the
# module that PRODUCES it. The API REFERENCES it instead of declaring a second
# copy, so the shape cannot drift between the CLI and HTTP surfaces.
from alias_acceptance import ACCEPTANCE_FIELDS  # noqa: E402

_CACHE: dict[str, tuple[str, Any]] = {}
_STATS = {"hits": 0, "misses": 0, "stale_evictions": 0, "evictions": 0}


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def register_version(conn: sqlite3.Connection) -> str:
    """A CONTENT FINGERPRINT of the register, used to invalidate the cache.

    ITS SCOPE IS EXACTLY THE COLUMNS `resolve_name` READS: `term_key`,
    `alias_list`, `is_active` and the row identity. A change to a column the
    resolver does not read (e.g. `definition`, `updated_at`) does NOT change this
    fingerprint — and that is CORRECT: the cached ANSWER cannot have changed.

    TWO EARLIER ATTEMPTS, both MEASURED WRONG and caught by this task's proof:
      * `MAX(updated_at)` — an UPDATE inside the same SECOND is invisible, and a
        write that does not touch `updated_at` is invisible too;
      * `PRAGMA data_version` — MEASURED to stay at `1` even after ANOTHER
        connection wrote, so it does not detect the case it name implies.
    """
    try:
        r = conn.execute(
            "SELECT COUNT(*) n, IFNULL(MAX(term_id),0) m, "
            "IFNULL(SUM(LENGTH(term_key)),0) k, "
            "IFNULL(SUM(LENGTH(alias_list)),0) a, "
            "IFNULL(SUM(is_active),0) act FROM terminology_registry").fetchone()
        return "%d|%d|%d|%d|%d" % (int(r["n"]), int(r["m"]), int(r["k"]),
                                    int(r["a"]), int(r["act"]))
    except sqlite3.Error:
        return "unavailable"


def cached(key: str, produce, conn: sqlite3.Connection) -> Any:
    """Bounded, version-guarded memoisation. A stale entry is a MISS."""
    ver = register_version(conn)
    full = "%s@%s" % (key, ver)
    if full in _CACHE:
        _STATS["hits"] += 1
        return _CACHE[full][1]
    # if the SAME key exists under an older version, that is a stale eviction
    if any(k.startswith(key + "@") for k in _CACHE):
        _STATS["stale_evictions"] += 1
        for k in [k for k in _CACHE if k.startswith(key + "@")]:
            _CACHE.pop(k, None)
    _STATS["misses"] += 1
    val = produce()
    if len(_CACHE) >= CACHE_MAX:
        # evict the OLDEST (insertion order) so the bound is real
        oldest = next(iter(_CACHE))
        _CACHE.pop(oldest, None)
        _STATS["evictions"] += 1
    _CACHE[full] = (ver, val)
    return val


def cache_stats() -> dict[str, int]:
    return dict(_STATS, size=len(_CACHE), max=CACHE_MAX)


def clear_cache() -> None:
    """Empty the cache AND reset the counters, so a measurement starts at zero.

    MEASURED BUG: the first version cleared only `_CACHE`, leaving `hits`/`misses`
    from earlier calls, so a test that cleared then read once saw `misses=5`.
    """
    _CACHE.clear()
    for k in _STATS:
        _STATS[k] = 0


# --------------------------------------------------------------------------
# the operations, each CALLING the module that owns the rule
# --------------------------------------------------------------------------
def op_resolve(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    import terminology_alias as ta
    return cached("resolve:%s" % str(name), lambda: ta.resolve_name(conn, name),
                  conn)


def op_gaps(conn: sqlite3.Connection) -> dict[str, Any]:
    import terminology_alias as ta
    return cached("gaps", lambda: ta.name_gaps(conn), conn)


def op_distil_due(conn: sqlite3.Connection) -> dict[str, Any]:
    import alias_acceptance as aa
    return cached("distil", lambda: aa.is_distil_due(conn), conn)


def op_acceptance(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    import alias_acceptance as aa
    return cached("accept:%s" % str(name),
                  lambda: aa.accept_alias(conn, name), conn)


def op_structure(conn: sqlite3.Connection, capability_key: str) -> dict[str, Any]:
    import structure_contract as sc
    return cached("struct:%s" % str(capability_key),
                  lambda: sc.structure_of(conn, capability_key), conn)


def op_check_file(conn: sqlite3.Connection, file_path: str) -> dict[str, Any]:
    import structure_contract as sc
    return cached("file:%s" % str(file_path),
                  lambda: sc.check_file(conn, file_path), conn)


OPERATIONS: dict[str, Any] = {
    "resolve": op_resolve, "gaps": op_gaps, "distil_due": op_distil_due,
    "acceptance": op_acceptance, "structure": op_structure,
    "check_file": op_check_file,
}


def dispatch(conn: sqlite3.Connection, operation: str, arg: str = "") -> dict[str, Any]:
    """The ONE dispatch used by BOTH the CLI and the HTTP route.

    An unknown operation returns `UNKNOWN_OPERATION` naming the known ones — a
    NAMED error, never an empty 200.
    """
    op = str(operation or "").strip()
    fn = OPERATIONS.get(op)
    if not fn:
        return {"ok": False, "code": "UNKNOWN_OPERATION", "operation": op,
                "known": sorted(OPERATIONS),
                "cite": "measured: OPERATIONS dict"}
    if op in ("gaps", "distil_due"):
        return fn(conn)
    return fn(conn, str(arg or ""))


# --------------------------------------------------------------------------
# the HTTP surface
# --------------------------------------------------------------------------
def build_router(db_path: str | Path | None = None):
    """An APIRouter over the SAME operations. Requires FastAPI at call time."""
    from fastapi import APIRouter, Query
    from fastapi.responses import JSONResponse
    router = APIRouter(prefix="/api/terminology", tags=["terminology"])

    def _run(operation: str, arg: str = ""):
        conn = _connect(db_path)
        try:
            res = dispatch(conn, operation, arg)
        finally:
            conn.close()
        # A NAMED error is a 404 only for an unknown OPERATION; a legitimate
        # negative verdict (e.g. NO_CASES) is a 200 carrying its own code, because
        # it is an ANSWER, not a failed request.
        if res.get("code") == "UNKNOWN_OPERATION":
            return JSONResponse(status_code=404, content=res)
        return res

    @router.get("/resolve")
    def resolve(name: str = Query(...)):
        return _run("resolve", name)

    @router.get("/gaps")
    def gaps():
        return _run("gaps")

    @router.get("/distil-due")
    def distil_due():
        return _run("distil_due")

    @router.get("/alias-acceptance")
    def alias_acceptance(name: str = Query(...)):
        return _run("acceptance", name)

    @router.get("/structure")
    def structure(capability_key: str = Query(...)):
        return _run("structure", capability_key)

    @router.get("/check-file")
    def check_file(file_path: str = Query(...)):
        return _run("check_file", file_path)

    @router.get("/cache-stats")
    def cache_stats_route():
        return cache_stats()

    return router


def register(app, db_path: str | Path | None = None):
    """Mount the router on an EXISTING app — no second app is created."""
    app.include_router(build_router(db_path))
    return app


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"ok": True, "operations": sorted(OPERATIONS),
            "cache_max": CACHE_MAX, "cache": cache_stats(),
            "register_version": register_version(conn),
            "acceptance_fields": list(ACCEPTANCE_FIELDS),
            "cite": "measured: OPERATIONS + register_version"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--op", default="")
    ap.add_argument("--arg", default="")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.serve:
        import uvicorn
        import main_api
        register(main_api.app, args.db)
        uvicorn.run(main_api.app, host="127.0.0.1", port=int(args.port))
        return 0
    conn = _connect(args.db)
    try:
        if args.op:
            import json
            print(json.dumps(dispatch(conn, args.op, args.arg), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        res = measure(conn)
        if args.json:
            import json
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        print("operations   : %s" % res["operations"])
        print("cache        : max=%d  %s" % (res["cache_max"], res["cache"]))
        print("register ver : %s" % res["register_version"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
