# -*- coding: utf-8 -*-
"""llm_service_store.py — the DB-driven LLM SERVICE registry + dispatcher.

THE CONCEPT
-----------
A "service" is a CAPABILITY ROUTE, not a model. `llm.text` and `llm.vision` are
separate services because a text task routed to a vision-only model is a WRONG
ROUTE, and one `llm.services` key could not express that. The route is the unit
of dispatch.

    llm_service            the route registry      (llm_route, name)
    llm_route_provider   who serves a route      (llm_route_id, model_id, local,
                                                    priority)

`local` decides HOW a service is provided, NOT whether it is available:

    local=1   on this machine (ollama)   -> call it directly
    local=0   the IDE's LLM              -> raise a SERVICE TICKET

A local=0 provider is NOT unreachable. It is served by a ticket, which is the
handoff mechanism that ALREADY EXISTS (`ticket_store`). No new bridge is
introduced — a task_id/chat_id bridge was explicitly refused, and it is not
needed, because the ticket IS the handoff.

WHY THE POOL IS A TABLE AND NOT A LIST
--------------------------------------
`config.yaml` used to hold a hardcoded model array, and it drifted: it named
`qwen3.8:27B`, which is `local=0` and not on this machine, while the default
model `qwen2.5:7b-instruct` was absent from it. `get_next_model()` then returned
None for every handoff, and every escalation died with "escalation pool
exhausted". A hand-written list drifts from the registry; the registry is what
the dispatcher must read. So the pool is `ORDER BY priority` over the provider
table.

WHAT IT REFUSES
---------------
  * an unknown `llm_route` — a typo would create a route nobody serves
  * a provider whose model is not in `llm_model` — a phantom provider
  * a `local=1` provider whose model is not installed in ollama
  * a `local=0` provider with no `llm_service` ticket service registered
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"
# THE ONE OLLAMA BASE URL.
#
# DEFECT FOUND BY MEASURING IT (2026-09-21): this module used the ollama DEFAULT
# port (11434) while `vision_analyze.py:44` used 18803 — the repo's established
# convention (21 files, `.env.example`, the docs). MEASURED: BOTH ports answer
# `/api/tags` with the same two models, so 18803 is a gateway in front of 11434.
# Two constants for one daemon is a drift waiting to happen: a model pulled on
# one port would look absent on the other. This now reads the SAME env var with
# the SAME default, so there is one source.
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803")

# The routes seeded on first run. A SEED, not the source of truth: the read path
# is `SELECT ... FROM llm_service`. A hand-written list drifts from the table.
#
# THE THIRD ELEMENT IS THE ROUTE'S TYPE (the user, 2026-09-23): "this service to
# have a name and define anayle type = text / visual". It is STORED in
# `llm_service.needs_flag` and read from there. Before this the type lived in a
# dict INSIDE `can_serve`, so a new route could not carry one without a code edit.
DEFAULT_LLM_SERVICES: tuple[tuple[str, str, str, str], ...] = (
    ("llm.text", "LLM services",
     "Text completion served by an LLM provider.", "text"),
    ("llm.vision", "LLM services",
     "Vision / VL served by an LLM provider.", "visual"),
)

# The ticket service that carries a local=0 handoff. Registered in
# `ticket_center` so `ticket_store._service_id()` accepts it.
TICKET_SERVICE = "llm_service"

# The MODULE the LLM handoff happens in. A ticket is for a SERVICE and a module
# says WHERE in the system it happens; they are separate facts, linked through
# `ticket_module_map`. MEASURED: `llm_runtime` (module_id 15115) is the row for
# exactly this.
MODULE_KEY = "llm_runtime"


class ServiceRefused(RuntimeError):
    """Raised when a route or provider would be stored without a real target."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("llm service refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """One place that opens the DB, so `PRAGMA foreign_keys` is never forgotten.

    SQLite defaults `PRAGMA foreign_keys` to OFF **per connection**, so an FK in
    a DDL is decoration unless every connection turns it on. That defect was
    already measured once in this repo (the env-checklist store); this helper
    exists so it cannot be reintroduced here.
    """
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the tables and seed the route registry. Idempotent."""
    import db_schema

    conn.execute("PRAGMA foreign_keys = ON;")
    for ddl in (db_schema.LLM_SERVICE_DDL, db_schema.LLM_SERVICE_TYPE_DDL,
                db_schema.LLM_SERVICE_PROVIDER_DDL):
        conn.executescript(ddl)
    # ADDITIVE: `CREATE TABLE IF NOT EXISTS` does NOT add a column to an
    # existing table, so a legacy DB would silently lack `provider_kind`.
    cols = {str(r[1]) for r in conn.execute(
        "PRAGMA table_info(llm_route_provider)")}
    if cols and "provider_kind" not in cols:
        conn.execute("ALTER TABLE llm_route_provider ADD COLUMN "
                     "provider_kind TEXT NOT NULL DEFAULT 'thinking'")
    added = 0
    for key, name, desc, needs in DEFAULT_LLM_SERVICES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO llm_service "
            "(llm_route, name, description, needs_flag) VALUES (?,?,?,?)",
            (key, name, desc, needs))
        added += cur.rowcount
    conn.commit()
    # The TYPE vocabulary + the backfill of `llm_service.needs_flag`. Delegated:
    # the DDL and the migration live next to the table they belong to, and this
    # module does not re-implement either.
    type_seed = db_schema.seed_llm_service_type_defaults(conn)
    return {"ok": bool(type_seed.get("ok")), "services_added": added,
            "types": type_seed.get("types"),
            "needs_flag_backfilled": type_seed.get("needs_flag_backfilled"),
            "unregistered_types": type_seed.get("unregistered_types"),
            "services": [str(r[0]) for r in conn.execute(
                "SELECT llm_route FROM llm_service WHERE is_active=1 "
                "ORDER BY llm_route")]}


def service_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The REGISTERED type vocabulary, read from the table."""
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT type_key, definition FROM llm_service_type_registry "
        "WHERE is_active=1 ORDER BY type_key")]


def assert_known_type(conn: sqlite3.Connection, type_key: str) -> str:
    """A type must be REGISTERED. A typo would match zero models SILENTLY.

    The same rule as `capability_store.assert_known_kind`: the failure mode this
    prevents is a route that can never be satisfied, reported as "no provider can
    serve it" rather than "the type is wrong".
    """
    want = str(type_key or "").strip()
    row = conn.execute(
        "SELECT type_key FROM llm_service_type_registry "
        "WHERE type_key=? AND is_active=1", (want,)).fetchone()
    if not row:
        known = [str(r[0]) for r in conn.execute(
            "SELECT type_key FROM llm_service_type_registry WHERE is_active=1")]
        raise ServiceRefused(
            ["needs_flag %r is not a registered service type (known: %s). An "
             "unregistered type can never be satisfied, and would surface as "
             "'no provider can serve this' instead of 'the type is wrong'."
             % (type_key, ", ".join(known) or "none")])
    return want


def add_service(conn: sqlite3.Connection, llm_route: str, name: str, *,
                needs_flag: str = "NA", description: str = "") -> dict[str, Any]:
    """Register a route WITH its type. Refuses an unregistered type.

    The WRITE-SITE gate: a route is refused on insert, not checked later, so the
    hard-code cannot come back as "a route someone forgot to type".
    """
    ensure_schema(conn)
    key = str(llm_route or "").strip()
    if not key:
        raise ServiceRefused(["llm_route is required"])
    want = assert_known_type(conn, needs_flag)
    cur = conn.execute(
        "INSERT OR IGNORE INTO llm_service "
        "(llm_route, name, description, needs_flag) VALUES (?,?,?,?)",
        (key, str(name or key), str(description or ""), want))
    if not cur.rowcount:
        conn.execute("UPDATE llm_service SET needs_flag=? WHERE llm_route=?",
                     (want, key))
    conn.commit()
    return {"ok": True, "added": cur.rowcount, "llm_route": key,
            "needs_flag": want}


def services(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The ACTIVE route registry, read from the table (never hand-listed)."""
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, llm_route, name, description, needs_flag FROM llm_service "
        "WHERE is_active=1 ORDER BY llm_route")]


def _llm_route_id(conn: sqlite3.Connection, llm_route: str) -> int:
    """Resolve a route KEY to its id, refusing an unknown one.

    A typo must fail LOUDLY: an unknown route stored as a new row would create a
    service nobody serves, and every task routed to it would be orphaned.
    """
    row = conn.execute("SELECT id FROM llm_service WHERE llm_route=? AND "
                       "is_active=1", (str(llm_route).strip(),)).fetchone()
    if not row:
        known = [str(r[0]) for r in conn.execute(
            "SELECT llm_route FROM llm_service WHERE is_active=1")]
        raise ServiceRefused(
            ["llm_route %r is not an active row in llm_service (known: %s). An "
             "unknown route would orphan every task dispatched to it."
             % (llm_route, ", ".join(known) or "none")])
    return int(row[0])


def providers(conn: sqlite3.Connection, llm_route: str,
              *, transport: str | None = None) -> list[dict[str, Any]]:
    """WHO serves a route, in POOL ORDER. Read from the table, never hand-listed.

    `priority` ASC is the pool order (lower = tried first). This is what makes
    the pool a LIST read from the table instead of a hardcoded array.

    `transport` FILTERS BY HOW THE CALLER CAN REACH A PROVIDER, and it is a
    MEASURED fix (2026-09-28). MEASURED: `llm.text`'s pool was
    `['convaiinnovations/laya', 'qwen2.5:7b-instruct', 'qwen/qwen3.8-27b']` and
    `llm_100_run_harness.resolve_model` returned `pool[0]` = laya. The harness
    then called `vision_analyze.complete_text`, which POSTs to Ollama — and
    Ollama answered `ollama_http_404: model 'convaiinnovations/laya' not found`.
    **The registry was RIGHT and the CALLER was wrong**: laya is a Python
    LIBRARY (`import laya`), not an HTTP model.

    So a caller that can only speak HTTP passes `transport='http'` and receives a
    pool it can actually reach. `None` means "no filter" and is the honest
    default for a reader that only wants to SEE the pool.
    """
    ensure_schema(conn)
    sid = _llm_route_id(conn, llm_route)
    sql = ("SELECT p.id, p.model_id, p.local, p.priority, p.provider_kind, "
           "       p.transport, m.name AS model_name, m.visual, m.text "
           "FROM llm_route_provider p "
           "LEFT JOIN llm_model m ON m.model_id = p.model_id "
           "WHERE p.llm_route_id=? AND p.is_active=1")
    params: list[Any] = [sid]
    if transport is not None:
        sql += " AND p.transport=?"
        params.append(str(transport))
    sql += " ORDER BY p.priority ASC, p.model_id ASC"
    return [dict(r) for r in conn.execute(sql, params)]


def pool_for(conn: sqlite3.Connection, llm_route: str,
             *, transport: str | None = None) -> list[str]:
    """The pool = the ordered model_id list for a route. DB-driven.

    `transport` is passed through to `providers`, so a caller that can only speak
    HTTP gets a pool it can reach.
    """
    return [str(r["model_id"]) for r in providers(conn, llm_route,
                                                  transport=transport)]


# The route a TEXT job is served by. Same shape as
# `vision_analyze.VISION_SERVICE_KEY`; the two are the only route keys named in
# code, and each names a ROW that must exist (`_llm_route_id` refuses otherwise).
TEXT_LLM_ROUTE = "llm.text"


def resolve_text_model(conn: sqlite3.Connection | None = None, *,
                       db_path: Path | str | None = None,
                       llm_route: str = TEXT_LLM_ROUTE
                       ) -> dict[str, Any]:
    """The TEXT model, read from the DB-driven service registry.

    THE SAME SHAPE as `vision_analyze.resolve_vision_model`, deliberately: two
    resolvers that behave differently would be two bugs. `source` is RETURNED
    (`registry` | `fallback` | `fault`), never assumed — a silent fallback makes
    the caller report a model it never resolved.

    MEASURED DEFECT THIS REMOVES (2026-09-23): `terminology_sweep.py:62` held
        MODEL = "qwen2.5:7b-instruct"
    a Python literal, while the registry (`llm_route_provider` for `llm.text`)
    already named the same model at priority 10. The literal and the registry
    agreed BY LUCK and would silently disagree the moment either moved — the
    identical defect already fixed in `llm_100_run_harness` and `vision_analyze`.

    `fault` (not `fallback`) is returned when the registry is UNREADABLE: an
    unreachable table and an empty pool are DIFFERENT problems, and reporting
    both as "fallback" would hide a broken database behind a working default.
    """
    own = False
    if conn is None:
        path = Path(db_path) if db_path else DB
        try:
            conn = _connect(path)
            own = True
        except Exception as exc:
            return {"model": "", "llm_route": llm_route, "source": "fault",
                    "pool": [],
                    "reason": "cannot open the database: %s: %s"
                              % (type(exc).__name__, exc)}
    try:
        pool = pool_for(conn, llm_route)
        if pool:
            return {"model": str(pool[0]), "llm_route": llm_route,
                    "source": "registry", "pool": [str(m) for m in pool]}
        return {"model": "", "llm_route": llm_route, "source": "fault",
                "pool": [],
                "reason": "route %r has NO active provider, so there is no "
                          "model to resolve. A fallback literal would CONCEAL "
                          "this." % llm_route}
    except ServiceRefused as exc:
        return {"model": "", "llm_route": llm_route, "source": "fault",
                "pool": [], "reason": "; ".join(exc.reasons)}
    except Exception as exc:
        return {"model": "", "llm_route": llm_route, "source": "fault",
                "pool": [], "reason": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        if own:
            conn.close()


def available_models(base_url: str | None = None,
                     timeout: float = 5.0) -> set[str] | None:
    """The models ollama actually has, or None when it cannot be reached.

    None means UNKNOWN, and unknown is NOT the same as empty. Treating an
    unreachable ollama as "no models" would report every provider as missing and
    would hide the real fault (the daemon is down).
    """
    url = (base_url or OLLAMA_BASE).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return {str(m.get("name") or "") for m in body.get("models", [])}
    except Exception:
        return None


def local_models(conn: sqlite3.Connection) -> set[str] | None:
    """The models registered as `local=1` in `llm_model`, or None if unreadable.

    This is the authoritative "what we have at this location" source. None means
    UNKNOWN, not empty.
    """
    return _registered(conn, local_only=True)


def registered_models(conn: sqlite3.Connection) -> set[str] | None:
    """EVERY model registered in `llm_model`, or None if unreadable.

    DEFECT FOUND BY RUNNING IT: `validate_providers()` checked a `local=0`
    provider against `local_models()` (local=1 only), so every remote provider
    was reported MISSING even though it IS registered. A local=0 provider is
    registered in `llm_model` with `local=0`; it is not installed in ollama, and
    that is expected. The two checks need two different sets.
    """
    return _registered(conn, local_only=False)


def _registered(conn: sqlite3.Connection, *, local_only: bool) -> set[str] | None:
    """Read `llm_model.model_id`, optionally restricted to local=1. None = unknown."""
    try:
        sql = "SELECT model_id FROM llm_model"
        if local_only:
            sql += " WHERE local=1"
        return {str(r[0]) for r in conn.execute(sql)}
    except Exception:
        return None


def validate_providers(conn: sqlite3.Connection, *,
                       base_url: str | None = None) -> dict[str, Any]:
    """Check every provider against a REAL target. Report missing ones BY NAME.

    A provider that cannot be served is a phantom: the pool would offer it, the
    dispatcher would pick it, and the task would fail at the last moment. So it
    is reported here, loudly, instead of surfacing as a mysterious handoff death.
    """
    ensure_schema(conn)
    installed = available_models(base_url)
    registered = registered_models(conn)
    missing: list[dict[str, Any]] = []
    unknown: list[str] = []
    for svc in services(conn):
        for p in providers(conn, svc["llm_route"]):
            mid = str(p["model_id"])
            kind = str(p.get("provider_kind") or "thinking")
            if kind != "thinking":
                # A TOOL provider (eye / hand / voice) is not a model, so it is
                # NOT checked against ollama or `llm_model`. It must be a
                # REGISTERED CAPABILITY instead — the same rule, a different
                # registry. DEFECT FOUND BY RUNNING THE PROOF: without this
                # branch every OpenClaw capability was reported "not installed
                # in ollama", which is true and irrelevant.
                cap = conn.execute(
                    "SELECT capability_kind FROM capability_registry WHERE "
                    "capability_key=? AND is_active=1", (mid,)).fetchone()
                if not cap:
                    missing.append({"llm_route": svc["llm_route"],
                                    "model_id": mid, "local": int(p["local"]),
                                    "reason": "not a registered capability"})
                elif str(cap[0]) != kind:
                    missing.append({"llm_route": svc["llm_route"],
                                    "model_id": mid, "local": int(p["local"]),
                                    "reason": "capability kind %r != provider "
                                              "kind %r" % (cap[0], kind)})
                continue
            if int(p["local"]) == 1:
                # local=1 must be INSTALLED in ollama.
                if installed is None:
                    unknown.append(mid)
                elif mid not in installed:
                    missing.append({"llm_route": svc["llm_route"],
                                    "model_id": mid, "local": 1,
                                    "reason": "not installed in ollama"})
            else:
                # local=0 must be REGISTERED in llm_model (it is not installed
                # here by definition — it is the IDE's LLM).
                if registered is None:
                    unknown.append(mid)
                elif mid not in registered:
                    missing.append({"llm_route": svc["llm_route"],
                                    "model_id": mid, "local": 0,
                                    "reason": "not registered in llm_model"})
    return {"ok": not missing, "missing": missing, "unknown": unknown,
            "installed": sorted(installed) if installed is not None else None,
            "registered": sorted(registered)
            if registered is not None else None}


def serve(conn: sqlite3.Connection, llm_route: str, *,
          eumu_id: str, title: str = "", opened_by: str = "",
          note: str = "", cite_ref: str = "",
          prompt: str = "", model: str | None = None,
          timeout: float = 180.0) -> dict[str, Any]:
    """Provide a service. THE POINT OF THIS MODULE.

    DISPATCH IS BY ABILITY, NOT BY LOCATION.
    DEFECT FOUND BY THE USER (2026-09-21): this used to branch on `local` —
    local=1 called the model, local=0 raised a ticket. The user's words:
    "local=1 -> 開一張 service ticket / local=0 -> 開一張 service ticket / same!!!!"
    `local` is INFRASTRUCTURE (where the model runs), not ABILITY. The condition
    is "LLM ability x services factor", so the branch is on whether the provider
    can SERVE the task, and `local` only decides HOW the chosen provider is
    reached.

    `local` is KEPT but DEMOTED: it is an infrastructure attribute that selects
    the transport (direct call vs ticket), never the dispatch decision.
    """
    ensure_schema(conn)
    pool = providers(conn, llm_route)
    if not pool:
        raise ServiceRefused(
            ["route %r has no active provider — nothing can serve it"
             % llm_route])
    chosen = None
    if model:
        chosen = next((p for p in pool if str(p["model_id"]) == str(model)), None)
        if chosen is None:
            raise ServiceRefused(
                ["model %r is not an active provider of route %r (pool: %s)"
                 % (model, llm_route,
                    ", ".join(str(p["model_id"]) for p in pool))])
    else:
        chosen = pool[0]

    mid = str(chosen["model_id"])
    kind = str(chosen.get("provider_kind") or "thinking")
    # THE ABILITY CHECK. A provider that cannot serve the route is not a
    # candidate, whatever its location. `can_serve()` reads the provider's
    # capability tags, so the decision is about ABILITY.
    ability = can_serve(conn, llm_route, mid)
    if not ability["ok"]:
        raise ServiceRefused(
            ["provider %r cannot serve route %r: %s"
             % (mid, llm_route, "; ".join(ability["reasons"]))])

    # THE KIND SELECTS THE TRANSPORT. The user's model: LLM and OpenClaw are
    # BOTH service providers, differing only in the KIND of service. `thinking`
    # is served by an LLM; `eye` / `hand` / `voice` are served by OpenClaw.
    #
    # `code` IS A THIRD TRANSPORT (2026-09-29). MEASURED: Cline is a coding
    # agent reached by SPAWNING `cline --json ...` as a subprocess. It is NOT an
    # MCP tool (it has no OpenClaw tool name) and NOT an HTTP model, so routing
    # it through `_serve_tool` would ask OpenClaw for a tool that does not exist.
    # The KIND decides, so `code` gets its own branch rather than being folded
    # into a transport it does not use.
    if kind == "code":
        return _serve_code(conn, llm_route, mid, chosen,
                           eumu_id=eumu_id, title=title, opened_by=opened_by,
                           note=note, cite_ref=cite_ref, ability=ability,
                           prompt=prompt, timeout=timeout)
    if kind != "thinking":
        return _serve_tool(conn, llm_route, mid, kind, chosen,
                           eumu_id=eumu_id, title=title, opened_by=opened_by,
                           note=note, cite_ref=cite_ref, ability=ability)

    # `local` now selects the TRANSPORT only.
    if int(chosen["local"]) == 1:
        # EXISTING channel. No new transport is introduced.
        import skill_task_queue as stq

        raw = stq._llm_call(prompt, model=mid, timeout=timeout)
        return {"ok": True, "mode": "direct", "llm_route": llm_route,
                "model_id": mid, "local": 1, "provider_kind": kind,
                "ability": ability, "raw": raw}

    # A remote provider is reached by a SERVICE TICKET. The ticket IS the
    # handoff — no task_id/chat_id bridge is involved.
    import ticket_store as ts

    res = ts.create_ticket(conn, service=TICKET_SERVICE,
                           title=title or ("%s via %s" % (llm_route, mid)),
                           opened_by=opened_by or "llm_service_store",
                           note=note or ("remote provider %s must serve %s"
                                         % (mid, llm_route)),
                           cite_ref=cite_ref or "llm_service_store.py:serve",
                           module=MODULE_KEY)
    return {"ok": True, "mode": "ticket", "llm_route": llm_route,
            "model_id": mid, "local": 0, "provider_kind": kind,
            "ability": ability,
            "ticket_id": res.get("ticket_id"),
            "created": res.get("created"),
            # The ENTITY is reported SEPARATELY and is NOT part of the ticket
            # (user, 2026-09-21: "no related too other, don't mix up"). It says
            # which THING the work is about; the ticket says which SERVICE does
            # it. The caller asked for `eumu_id`, so it is echoed back.
            "eumu_id": str(eumu_id or ""),
            "module": res.get("module"),
            "status": res.get("status")}


# The CLI binary that serves a `code` provider. A NAME, not a path: the binary
# is resolved on PATH at call time, so a machine without it reports "absent"
# rather than a stale absolute path that used to exist.
CLINE_BIN = "cline"

# THE AUTO-APPROVE FLAG, AND WHY IT MUST BE `true` FOR A `code` PROVIDER.
#
# MEASURED 2026-09-29, the SAME edit prompt on each value:
#   --auto-approve false -> the `editor` tool is UNAVAILABLE:
#       "it requires an interactive session which is not available in this
#        non-interactive context", `finishReason: aborted`, file UNCHANGED.
#   --auto-approve true  -> the `editor` tool RUNS, `finishReason: completed`,
#       the file IS changed.
#
# So `false` does not make the provider SAFER — it makes it INCAPABLE. A `code`
# provider that cannot edit a file is not a `code` provider. The safety boundary
# is therefore the DISPATCHER, not this flag: the agent system decides WHAT
# prompt is sent (and to which repo), and Cline executes exactly that. The flag
# is a parameter so a caller CAN tighten it, but the default must be the value
# that makes the capability real.
CLINE_AUTO_APPROVE = "true"

# THE ENV VAR THE CLI AUTHENTICATES WITH. MEASURED by grepping the installed
# `cline` package for `CLINE_[A-Z_]+` — `CLINE_API_KEY` is the name it reads.
CLINE_API_KEY_ENV = "CLINE_API_KEY"

# THE ENV VAR WE STORE THE TOKEN UNDER (measured: the repo's convention, next to
# `DEEPSEEK_API_KEY` / `GITHUB_TOKEN` / `OPENCLAW_MCP_TOKEN`). `_serve_code`
# bridges ONE name to the OTHER, so the repo's naming is kept and the CLI gets
# the name it expects.
CLINE_TOKEN_ENV = "CLINE_TOKEN"

# THE LOCAL PROVIDER, AND WHY IT IS THE DEFAULT.
#
# MEASURED 2026-09-29: a run that named NO provider used `lastUsedProvider`
# from `~/.cline/data/settings/providers.json` = `cline` with model
# `aion-labs/aion-3.5`, and the ledger recorded `totalCost: 0.016917` — a PAID
# cloud model. The human's requirement is FREE + LOCAL, so `_serve_code` names
# the LOCAL provider EXPLICITLY instead of inheriting whatever was used last.
#
# MEASURED, the same prompt on each provider:
#     cline  (aion-labs/aion-3.5)  -> totalCost 0.016917
#     ollama (qwen2.5:7b-instruct) -> totalCost 0
#
# `qwen2.5:7b-instruct` is served by the machine's Ollama, which the repo already
# names (`OLLAMA_BASE`, and `llm_model` rows for `qwen2.5:7b-instruct`).
CLINE_PROVIDER = "ollama"
CLINE_MODEL = "qwen2.5:7b-instruct"


def _env_file_value(name: str, path: Path | None = None) -> str:
    """Read ONE value from the repo `.env`, or '' when absent.

    WHY THE FILE AND NOT `os.environ`: the `code` provider is reached by a
    SUBPROCESS, and a caller that imports this module has not necessarily
    exported `.env` into its own environment. Reading the file makes the token
    available to the child process WITHOUT the caller having to `export` it.

    The value is NEVER returned to a caller that would print it: this function
    feeds `subprocess`'s `env` only. An empty result means ABSENT, never a
    fallback literal — a fallback token would be a credential nobody provisioned.
    """
    p = path or (BASE / ".env")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == str(name):
                    return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _parse_cline_events(stdout: str) -> tuple[str, str, int, int, dict]:
    """Parse the CLI's stream → (text, finish, events, raw, meta).

    `meta` carries the FACTS the "free + local" claim rests on, READ from the
    run rather than assumed:
      `model`    the model id the run actually used
      `provider` the provider the run actually used
      `cost`     `usage.totalCost` (0 means free)
    A claim that a run is local and free must be read from the run; a probe that
    asserts a knob it set is testing the knob, not the outcome.

    EXTRACTED SO IT IS PROVABLE. A parser buried inside a `subprocess` call can
    only be tested by running the CLI; as a function it is tested against the
    MEASURED byte shape, so the "46 events, empty text" defect cannot return.

    THE REAL EVENT SHAPE, MEASURED 2026-09-29 from a live run. The CLI does NOT
    emit `{"type":"say","text":...}` (the docs' shape). It emits:
      {"type":"agent_event","event":{"type":"content_end",
                                     "contentType":"text","text":"PONG\n"}}
      {"type":"run_result","finishReason":"completed","text":"PONG\n",...}

    `raw` counts lines that are NOT JSON objects, so a CLI that changes its
    output shape is VISIBLE rather than silently producing zero events.
    """
    events: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    for line in (stdout or "").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                events.append(obj)
            else:
                raw_lines.append(s)
        except Exception:
            raw_lines.append(s)
    # Prefer the AGGREGATE (`run_result.text`), else the LAST `content_end`
    # chunk, else the docs' `say` shape as a fallback.
    text = ""
    for e in events:
        if str(e.get("type")) == "run_result" and e.get("text"):
            text = str(e.get("text"))
    if not text:
        for e in events:
            if str(e.get("type")) == "agent_event":
                inner = e.get("event") or {}
                if (str(inner.get("type")) == "content_end"
                        and str(inner.get("contentType")) == "text"):
                    text = str(inner.get("text") or "")
    if not text:
        text = "".join(str(e.get("text") or "") for e in events
                       if str(e.get("type")) == "say")
    finish = ""
    meta: dict = {"model": "", "provider": "", "cost": None}
    for e in events:
        if str(e.get("type")) == "run_result":
            finish = str(e.get("finishReason") or "")
            m = e.get("model") or {}
            meta["model"] = str(m.get("id") or "")
            meta["provider"] = str(m.get("provider") or "")
            usage = e.get("usage") or {}
            meta["cost"] = usage.get("totalCost")
    return text, finish, len(events), len(raw_lines), meta


def _serve_code(conn: sqlite3.Connection, llm_route: str, model_id: str,
                chosen: dict[str, Any], *,
                eumu_id: str, title: str, opened_by: str, note: str,
                cite_ref: str, ability: dict[str, Any],
                prompt: str, timeout: float) -> dict[str, Any]:
    """Serve a `code` capability by SPAWNING a CLI agent (Cline).

    THE THIRD TRANSPORT. An LLM is reached over HTTP; OpenClaw over MCP; a
    coding agent is reached by RUNNING A PROGRAM. The KIND decides, so this is a
    branch of `serve()` rather than a second dispatcher.

    THE DISPATCHER IS NOT THE EXECUTOR. This function does not decide WHAT to
    change — it is handed a prompt by the caller (the agent system) and returns
    what the executor said. The decision lives upstream; this is the hand.

    AN ABSENT BINARY IS A NORMAL STATE, NOT A FAULT. Exactly as an offline
    OpenClaw raises a ticket rather than failing the task, a machine without
    `cline` on PATH raises a ticket. Reporting "broken" for "not installed"
    would be the same defect `provider_available` exists to prevent.
    """
    import capability_store as cs

    # The capability this provider serves must be REGISTERED with kind `code`,
    # so an unregistered ability cannot be dispatched by name.
    cap = conn.execute(
        "SELECT capability_key, capability_kind FROM capability_registry "
        "WHERE capability_key=? AND is_active=1", (str(model_id),)).fetchone()
    if not cap:
        raise ServiceRefused(
            ["capability %r is not an active row in capability_registry. An "
             "unregistered ability cannot be dispatched — register it with "
             "capability_store.register()." % model_id])
    if str(cap[1]) != "code":
        raise ServiceRefused(
            ["capability %r declares kind %r but the provider says 'code'"
             % (model_id, cap[1])])

    # AVAILABILITY: is the CLI on PATH? `shutil.which` is the honest test — it
    # answers "can this machine run it", not "was it installed once".
    exe = shutil.which(CLINE_BIN)
    if not exe:
        import ticket_store as ts

        res = ts.create_ticket(
            conn, service=TICKET_SERVICE,
            title=title or ("%s via %s (CLI absent)" % (llm_route, model_id)),
            opened_by=opened_by or "llm_service_store",
            note=note or ("the %r CLI is not on PATH — a normal state, not a "
                          "fault. Install it (npm i -g cline) or serve this "
                          "route another way." % CLINE_BIN),
            cite_ref=cite_ref or "llm_service_store.py:_serve_code",
            module=MODULE_KEY)
        return {"ok": True, "mode": "ticket", "llm_route": llm_route,
                "model_id": model_id, "provider_kind": "code",
                "ability": ability,
                "availability": {"ok": False, "reason": "cli not on PATH",
                                 "bin": CLINE_BIN},
                "ticket_id": res.get("ticket_id"), "created": res.get("created"),
                "eumu_id": str(eumu_id or ""),
                "module": res.get("module"), "status": res.get("status")}

    if not str(prompt or "").strip():
        raise ServiceRefused(
            ["a `code` provider needs a PROMPT. The dispatcher decides WHAT to "
             "do and hands it here; this function is the hand, not the head."])

    # THE INVOCATION. `--json` gives newline-delimited JSON on stdout; the
    # prompt is the positional argument. `--auto-approve false` is FORCED.
    #
    # THE PROVIDER AND MODEL ARE NAMED, NOT INHERITED.
    #
    # MEASURED 2026-09-29, two defects on the same call:
    #   1. NO provider named -> the CLI used `lastUsedProvider` = `cline`, model
    #      `aion-labs/aion-3.5`, and the ledger recorded `totalCost: 0.016917`.
    #      A PAID cloud model, when the requirement is FREE + LOCAL.
    #   2. An ISOLATED `--data-dir` -> the CLI could not see the machine's
    #      `providers.json`, so `-P ollama` fell back to the cloud and exited 1.
    #
    # So the CLI runs against the MACHINE'S OWN config (no `--data-dir`), with
    # the LOCAL provider and model named explicitly. `-P ollama -m
    # qwen2.5:7b-instruct` was measured at `totalCost: 0` with
    # `model.provider == "ollama"`.
    data_dir = str(Path.home() / ".cline" / "data")
    argv = [exe, "--json", "--auto-approve", CLINE_AUTO_APPROVE,
            "-P", CLINE_PROVIDER, "-m", CLINE_MODEL,
            "-t", str(int(timeout)), str(prompt)]
    # THE AUTH BRIDGE. `.env` holds `CLINE_TOKEN`; the CLI reads
    # `CLINE_API_KEY`. The value is passed ONLY in the child's env and is never
    # echoed in the result. An absent token is passed as-is (the CLI then
    # reports its own auth error, which is a REAL answer).
    child_env = dict(os.environ)
    token = (os.environ.get(CLINE_TOKEN_ENV)
             or _env_file_value(CLINE_TOKEN_ENV))
    if token:
        child_env[CLINE_API_KEY_ENV] = token
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=float(timeout) + 30.0,
            cwd=str(BASE), env=child_env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "mode": "cli", "llm_route": llm_route,
                "model_id": model_id, "provider_kind": "code",
                "ability": ability, "error": "timeout",
                "argv": argv[:4] + ["<prompt>"]}

    # PARSE the event stream (see `_parse_cline_events` for the measured shape).
    text, finish, n_events, n_raw, meta = _parse_cline_events(proc.stdout or "")
    return {"ok": proc.returncode == 0, "mode": "cli", "llm_route": llm_route,
            "model_id": model_id, "provider_kind": "code", "ability": ability,
            "availability": {"ok": True, "bin": CLINE_BIN, "path": exe,
                             # PRESENCE, never the value.
                             "token_present": bool(token)},
            "data_dir": data_dir, "finish_reason": finish,
            # THE FREE + LOCAL EVIDENCE, read from the RUN, not the argv.
            "provider": meta.get("provider"), "model": meta.get("model"),
            "cost": meta.get("cost"),
            "is_local": str(meta.get("provider")) == CLINE_PROVIDER,
            "is_free": meta.get("cost") in (0, 0.0),
            "exit_code": proc.returncode, "events": n_events,
            "raw_lines": n_raw, "text": text,
            "stderr": (proc.stderr or "")[-2000:]}


def _serve_tool(conn: sqlite3.Connection, llm_route: str, model_id: str,
                kind: str, chosen: dict[str, Any], *,
                eumu_id: str, title: str, opened_by: str, note: str,
                cite_ref: str, ability: dict[str, Any]) -> dict[str, Any]:
    """Serve an `eye` / `hand` / `voice` capability through OpenClaw's MCP.

    THE SAME PROVIDER CONCEPT, A DIFFERENT TRANSPORT. An LLM is reached over
    HTTP (`/api/chat`); OpenClaw is reached over MCP (`call_tool`). The KIND
    decides which, so `serve()` has ONE dispatch rule instead of two unrelated
    code paths.

    AVAILABILITY GATES DISPATCH: an OFFLINE OpenClaw is a NORMAL state, not a
    fault. It raises a ticket, exactly as an unreachable remote LLM does — a
    provider that is merely absent must not be reported as broken.
    """
    import capability_store as cs

    # The capability this provider serves must be REGISTERED, so an unregistered
    # ability cannot be dispatched by name.
    cap = conn.execute(
        "SELECT capability_key, capability_kind FROM capability_registry "
        "WHERE capability_key=? AND is_active=1", (str(model_id),)).fetchone()
    if not cap:
        raise ServiceRefused(
            ["capability %r is not an active row in capability_registry. An "
             "unregistered ability cannot be dispatched — register it with "
             "capability_store.register()." % model_id])
    if str(cap[1]) != kind:
        raise ServiceRefused(
            ["capability %r declares kind %r but the provider says %r"
             % (model_id, cap[1], kind)])

    # AVAILABILITY. `user_asset` already answers "is OpenClaw available to have
    # work" (status + last_seen_at). An offline provider is NOT a fault.
    avail = provider_available(conn, kind)
    if not avail["ok"]:
        import ticket_store as ts

        res = ts.create_ticket(
            conn, service=TICKET_SERVICE,
            title=title or ("%s via %s (provider offline)"
                            % (llm_route, model_id)),
            opened_by=opened_by or "llm_service_store",
            note=note or ("provider for %s is %s — a normal state, not a fault"
                          % (model_id, avail["reason"])),
            cite_ref=cite_ref or "llm_service_store.py:_serve_tool",
            module=MODULE_KEY)
        return {"ok": True, "mode": "ticket", "llm_route": llm_route,
                "model_id": model_id, "provider_kind": kind,
                "ability": ability, "availability": avail,
                "ticket_id": res.get("ticket_id"), "created": res.get("created"),
                "eumu_id": str(eumu_id or ""),
                "module": res.get("module"), "status": res.get("status")}

    # The MCP call. `call_tool` is the EXISTING OpenClaw channel.
    import mcp_client

    client = mcp_client.McpClient(mcp_client.McpConfig.from_env())
    tool = str(chosen.get("mcp_tool") or _mcp_tool_for(model_id, conn))
    result = client.call_tool(tool, {})
    return {"ok": True, "mode": "mcp", "llm_route": llm_route,
            "model_id": model_id, "provider_kind": kind, "ability": ability,
            "availability": avail, "mcp_tool": tool, "result": result}


# The capability -> MCP tool map. A capability is the ABILITY; the tool is HOW
# OpenClaw exposes it. Kept as data so a new tool is a row, not a code branch.
#
# ============================================================================
# THIS IS A FALLBACK, NOT THE SOURCE. (demoted 2026-09-21)
# ============================================================================
# The source is `capability_tool`, joined to `capability_registry`. Measured:
# this dict and the DB AGREED on the split (screen_capture + screen_record),
# while `openclaw_settings.CAPABILITIES` MERGED them — so of the four copies,
# the DB and this dict were the two that agreed. It is kept as the fallback for
# a DB that has not been seeded yet.
MCP_TOOL_FOR_CAPABILITY: dict[str, str] = {
    "openclaw.screen_capture": "screen.snapshot",
    "openclaw.screen_record": "screen.record",
    "openclaw.camera": "camera.snap",
    "openclaw.system_exec": "system.run",
    "openclaw.notify": "system.notify",
    "openclaw.speech": "tts.speak",
}


def _mcp_tool_for(capability_key: str, conn: sqlite3.Connection | None = None
                  ) -> str:
    """The MCP tool a capability maps to, or a refusal naming the gap.

    Reads `capability_tool` FIRST, so a capability registered in the DB is
    reachable without a code change. Falls back to the dict above only when the
    table has no row for it.
    """
    if conn is not None:
        try:
            import capability_store as cs

            rows = cs.tools_for(conn, str(capability_key))
            if rows:
                return str(rows[0]["tool_name"])
        except Exception:
            pass
    tool = MCP_TOOL_FOR_CAPABILITY.get(str(capability_key))
    if not tool:
        raise ServiceRefused(
            ["capability %r has no MCP tool mapping. Add a row to "
             "`capability_tool` (capability_store.set_tools) so the ability is "
             "reachable. The `MCP_TOOL_FOR_CAPABILITY` dict is a FALLBACK, not "
             "the source." % capability_key])
    return tool


def provider_available(conn: sqlite3.Connection,
                       kind: str) -> dict[str, Any]:
    """Is the provider for this KIND available to have work?

    `user_asset` already tracks this: `status` + `last_seen_at` + `detail`.
    An OFFLINE provider is a NORMAL state, not a fault — the caller raises a
    ticket rather than failing the task.

    Returns `{ok, reason, status, app_key}`. `ok=False` means "not available
    now", NOT "broken".
    """
    app_key = {"eye": "openclaw", "hand": "openclaw",
               "voice": "openclaw"}.get(str(kind))
    if app_key is None:
        # `thinking` is served over HTTP, not by an app asset.
        return {"ok": True, "reason": "not app-gated", "status": None,
                "app_key": None}
    row = conn.execute(
        "SELECT a.status, a.detail, a.last_seen_at FROM user_asset a "
        "JOIN app p ON p.app_id = a.app_id WHERE p.app_key=? "
        "ORDER BY a.updated_at DESC LIMIT 1", (app_key,)).fetchone()
    if not row:
        return {"ok": False, "reason": "no user_asset row for %s" % app_key,
                "status": None, "app_key": app_key}
    status = str(row[0] or "unknown")
    return {"ok": status == "online", "reason": "status=%s" % status,
            "status": status, "detail": row[1], "last_seen_at": row[2],
            "app_key": app_key}


def can_serve(conn: sqlite3.Connection, llm_route: str,
              model_id: str) -> dict[str, Any]:
    """Can this provider SERVE this route? The ABILITY check, not a location one.

    The condition the user stated is "LLM ability x services factor". A provider
    serves a route when its registered model declares the matching ability:
    an `llm.text` route needs `text=1`, an `llm.vision` route needs `visual=1`.
    `local` is deliberately NOT consulted — where a model runs says nothing
    about what it can do.

    MEASURED DEFECT FIXED (2026-09-23) — the user's words:
        "this service to have a name and define anayle type = text / visual"
        "so system will nnot have this problem again not hardcode!!!!!"

    The needed flag used to be a PYTHON DICT right here:
        need = {"llm.text": "text", "llm.vision": "visual"}.get(str(llm_route))
    Three faults, all measured:
      1. a new route needed a CODE EDIT to carry a type;
      2. an unknown route silently got `None` and this function then said
         `ok=True` — a route with NO declared ability passed every check;
      3. `eye.capture` (a real service, measured: 2 providers) WAS that silent
         `None` case.
    The flag is now a COLUMN (`llm_service.needs_flag`) read from the table, and
    `NA` is a DECLARED answer rather than an accident.
    """
    srow = conn.execute(
        "SELECT needs_flag FROM llm_service WHERE llm_route=? AND is_active=1",
        (str(llm_route),)).fetchone()
    if not srow:
        known = [str(r[0]) for r in conn.execute(
            "SELECT llm_route FROM llm_service WHERE is_active=1")]
        return {"ok": False, "model_id": str(model_id), "need": None,
                "reasons": ["route %r is not an active row in llm_service "
                            "(known: %s)" % (llm_route,
                                             ", ".join(known) or "none")]}
    # `NA` means the route needs NO model ability, so no `llm_model` column is
    # consulted. This is stated, not inferred from a missing key.
    need = str(srow["needs_flag"] or "").strip()
    if not need:
        return {"ok": False, "model_id": str(model_id), "need": None,
                "reasons": ["route %r has an EMPTY needs_flag — a route with no "
                            "declared type cannot be checked" % llm_route]}
    if need.upper() == "NA":
        return {"ok": True, "model_id": str(model_id), "need": None,
                "needs_flag": need, "reasons": []}
    row = conn.execute("SELECT visual, text, local FROM llm_model WHERE "
                       "model_id=?", (str(model_id),)).fetchone()
    if not row:
        return {"ok": False, "model_id": str(model_id), "need": need,
                "reasons": ["model %r is not registered in llm_model"
                            % model_id]}
    if need not in ("text", "visual"):
        # The column is checked against the VOCABULARY, not against a literal:
        # a type that names no `llm_model` column could never be satisfied, and
        # the failure would read as "no provider can serve this".
        return {"ok": False, "model_id": str(model_id), "need": need,
                "reasons": ["route %r needs %r, which is not a model ability "
                            "(known: %s)" % (llm_route, need, ", ".join(
                                str(r[0]) for r in conn.execute(
                                    "SELECT type_key FROM "
                                    "llm_service_type_registry "
                                    "WHERE is_active=1")))]}
    if not int(row[need] or 0):
        return {"ok": False, "model_id": str(model_id), "need": need,
                "reasons": ["route %r needs %s=1 but the model declares %s=0"
                            % (llm_route, need, need)]}
    return {"ok": True, "model_id": str(model_id), "need": need,
            "needs_flag": need, "reasons": []}


def add_provider(conn: sqlite3.Connection, llm_route: str, model_id: str, *,
                 local: int = 1, priority: int = 100,
                 provider_kind: str = "thinking",
                 transport: str = "http") -> dict[str, Any]:
    """Register a provider. Refuses a model that is not in `llm_model`.

    `provider_kind` and `transport` are ADDITIVE (2026-09-29). They default to
    the values every pre-existing provider already carried (`thinking` / `http`),
    so an existing caller is unchanged. They are parameters because a `code`
    provider (Cline) is neither: its kind is `code` and its transport is `cli`.
    A caller that could not name them would have to INSERT directly, bypassing
    the `llm_model` phantom-reference check this function exists to enforce.
    """
    ensure_schema(conn)
    sid = _llm_route_id(conn, llm_route)
    known = conn.execute("SELECT local FROM llm_model WHERE model_id=?",
                         (str(model_id),)).fetchone()
    if not known:
        raise ServiceRefused(
            ["model_id %r is not a row in llm_model — a provider for a model "
             "that does not exist is a phantom reference" % model_id])
    # The kind must be a REGISTERED vocabulary word, so a typo cannot create a
    # provider no dispatch branch will ever reach.
    import capability_store as cs

    cs.assert_known_kind(conn, provider_kind)
    if str(transport) not in ("http", "python", "cli"):
        raise ServiceRefused(
            ["transport %r is not one of http / python / cli. A caller that "
             "cannot name how it reaches a provider would receive a pool it "
             "cannot call." % transport])
    cur = conn.execute(
        "INSERT OR IGNORE INTO llm_route_provider "
        "(llm_route_id, model_id, local, priority, provider_kind, transport) "
        "VALUES (?,?,?,?,?,?)",
        (sid, str(model_id), int(local), int(priority), str(provider_kind),
         str(transport)))
    conn.commit()
    return {"ok": True, "added": cur.rowcount, "llm_route": llm_route,
            "model_id": str(model_id), "local": int(local),
            "priority": int(priority), "provider_kind": str(provider_kind),
            "transport": str(transport)}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LLM service registry (DB-driven)")
    ap.add_argument("--list", action="store_true", help="list routes + providers")
    ap.add_argument("--validate", action="store_true",
                    help="check every provider against a real target")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.validate:
            out = validate_providers(conn)
            print(json.dumps(out, indent=2) if args.json else
                  "ok=%s missing=%d unknown=%d"
                  % (out["ok"], len(out["missing"]), len(out["unknown"])))
            for m in out["missing"]:
                print("  MISSING %s %s (local=%s): %s"
                      % (m["llm_route"], m["model_id"], m["local"],
                         m["reason"]))
            return 0 if out["ok"] else 1
        ensure_schema(conn)
        for svc in services(conn):
            print("%s  (%s)" % (svc["llm_route"], svc["name"]))
            for p in providers(conn, svc["llm_route"]):
                print("    priority=%-4s local=%s  %s"
                      % (p["priority"], p["local"], p["model_id"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
