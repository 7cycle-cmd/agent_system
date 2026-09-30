# -*- coding: utf-8 -*-
"""capability_store.py — the DB-driven CAPABILITY registry, with a KIND.

THE USER'S MODEL (2026-09-21)
-----------------------------
    "so thet are same is services provide
     services provide -> thinking = LLM
     services provide -> EYE / HAND.... = openclaw"

LLM and OpenClaw are BOTH service providers. They differ only in the KIND of
service they provide. So a capability is not "an LLM thing" or "an OpenClaw
thing" — it is a service, and the kind says what sort.

    thinking   the provider REASONS      (an LLM)
    eye        the provider CAPTURES     (OpenClaw screen.snapshot / camera)
    hand       the provider ACTS         (OpenClaw system.run)
    voice      the provider SPEAKS/LISTENS (OpenClaw notify / tts / stt)
    code       the provider produces or gates SOURCE CODE

WHY THIS MODULE EXISTS
----------------------
MEASURED DEFECT (2026-09-21): `capability_registry` held 24 rows, and module 3
(`openclaw_companion`) had exactly ONE — the meta-capability `capability.ssot`.
OpenClaw's REAL abilities (screen.snapshot / system.run / system.notify /
camera) existed ONLY as a hardcoded list in `openclaw_settings.CAPABILITIES`.
That is the same defect family as `CAPABILITY_TAGS` before it was made
DB-driven: a registry exists, but the write path is a Python literal, so the
table can never hold a capability the literal does not already name.

The read path is `SELECT ... FROM capability_registry`. The seeds below are
SEEDS, not the source of truth.

WHAT IT REFUSES
---------------
  * an unknown `capability_kind` — a typo would match zero capabilities SILENTLY
  * a capability for a module that does not exist — a phantom reference
  * an empty definition — a capability with no meaning is decorative
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The module that owns the LLM runtime. OpenClaw already has
# `openclaw_companion` (module_id=3); the LLM had NO module at all, so its
# capabilities had nowhere to hang.
LLM_MODULE_KEY = "llm_runtime"
OPENCLAW_MODULE_KEY = "openclaw_companion"


class CapabilityRefused(RuntimeError):
    """Raised when a capability would be stored without a real kind or module."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("capability refused — not written: %s"
                         % "; ".join(self.reasons))


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the kind registry + the additive columns on `capability_registry`.

    ADDITIVE ONLY. `CREATE TABLE IF NOT EXISTS` does NOT add a column to an
    existing table, so a legacy DB would silently lack `capability_kind`,
    `gate_ref`, `why` and `probe_tool` — and the seed would die with
    "no such column". Every new column is therefore an explicit ALTER, applied
    by the module that READS it.
    """
    import db_schema

    conn.executescript(db_schema.CAPABILITY_KIND_DDL)
    conn.executescript(db_schema.CAPABILITY_TOOL_DDL)
    cols = {str(r[1]) for r in conn.execute(
        "PRAGMA table_info(capability_registry)")}
    if cols and "capability_kind" not in cols:
        conn.execute("ALTER TABLE capability_registry ADD COLUMN "
                     "capability_kind TEXT NOT NULL DEFAULT 'thinking'")
    # THE DECLARATION COLUMNS. `gate_ref` / `why` / `probe_tool` were the three
    # fields `openclaw_settings.CAPABILITIES` carried and this table did not, so
    # the table could not hold what the literal held.
    for name, decl in db_schema.CAPABILITY_DECLARATION_COLUMNS:
        if cols and name not in cols:
            conn.execute("ALTER TABLE capability_registry ADD COLUMN %s %s"
                         % (name, decl))
    conn.commit()
    return db_schema.seed_capability_kind_defaults(conn)


def kinds(conn: sqlite3.Connection) -> list[str]:
    """The ACTIVE kind vocabulary, read from the registry (never hand-listed)."""
    ensure_schema(conn)
    return [str(r[0]) for r in conn.execute(
        "SELECT kind_key FROM capability_kind_registry WHERE is_active=1 "
        "ORDER BY kind_key")]


def assert_known_kind(conn: sqlite3.Connection, kind: str) -> str:
    """A kind with no definition is REFUSED — and the refusal says HOW to add it.

    A typo (`thnking`) would match zero capabilities and report nothing, which
    is the exact silent-dead-match defect this registry exists to prevent. The
    message carries the way forward, because a gate that only refuses leaves the
    worker stuck.
    """
    known = kinds(conn)
    if str(kind) not in known:
        raise CapabilityRefused(
            ["capability_kind %r is not in capability_kind_registry (known: %s). "
             "An undefined kind matches zero capabilities SILENTLY.\n"
             "TO ADD IT: INSERT INTO capability_kind_registry (kind_key, "
             "definition) VALUES (%r, '<what the kind means>') — a kind needs a "
             "definition, because an invented one is a guess with a heading."
             % (kind, ", ".join(known) or "none", str(kind))])
    return str(kind)


def module_id(conn: sqlite3.Connection, module_key: str) -> int:
    """Resolve a module KEY to its id, refusing an unknown one."""
    row = conn.execute("SELECT module_id FROM module_registry WHERE "
                       "module_key=?", (str(module_key),)).fetchone()
    if not row:
        raise CapabilityRefused(
            ["module %r is not in module_registry. A capability for a module "
             "that does not exist is a phantom reference." % module_key])
    return int(row[0])


def ensure_module(conn: sqlite3.Connection, module_key: str, name: str,
                  *, channel_id: int = 1, description: str = "") -> dict:
    """Register a module if absent. Idempotent; never overwrites."""
    row = conn.execute("SELECT module_id FROM module_registry WHERE "
                       "module_key=?", (str(module_key),)).fetchone()
    if row:
        return {"ok": True, "created": False, "module_id": int(row[0])}
    cur = conn.execute(
        "INSERT INTO module_registry (module_key, name, description, "
        "channel_id) VALUES (?,?,?,?)",
        (str(module_key), str(name), str(description), int(channel_id)))
    conn.commit()
    return {"ok": True, "created": True, "module_id": int(cur.lastrowid)}


def register(conn: sqlite3.Connection, capability_key: str, name: str, *,
             module_key: str, kind: str, description: str = "",
             gate_ref: str = "NA", why: str = "NA", probe_tool: str = "NA",
             tools: list[str] | tuple[str, ...] | None = None,
             probe_safe_tools: list[str] | tuple[str, ...] | None = None,
             update: bool = False) -> dict[str, Any]:
    """Register a capability. THE WRITE PATH — a Python list is not the source.

    REFUSES an unknown kind, an unknown module, and a silent duplicate.

    `tools` is the DECLARATION of which tools provide the capability, and
    `probe_safe_tools` is the subset PROVEN read-only. Both are written to
    `capability_tool` in the SAME transaction, so a capability can never exist
    with its tools half-written.
    """
    ensure_schema(conn)
    key = str(capability_key or "").strip()
    if not key:
        raise CapabilityRefused(["capability_key is required"])
    if not str(name or "").strip():
        raise CapabilityRefused(
            ["name is required for capability %r — an unnamed capability "
             "cannot be listed or searched" % key])
    assert_known_kind(conn, kind)
    mid = module_id(conn, module_key)
    row = conn.execute("SELECT capability_id, capability_kind FROM "
                       "capability_registry WHERE capability_key=?",
                       (key,)).fetchone()
    if row and not update:
        raise CapabilityRefused(
            ["capability %r is already registered (kind: %s). Refusing to "
             "overwrite it silently — pass update=True if it really changed."
             % (key, row[1])])
    if row:
        conn.execute(
            "UPDATE capability_registry SET name=?, description=?, module_id=?, "
            "capability_kind=?, gate_ref=?, why=?, probe_tool=?, "
            "updated_at=datetime('now') WHERE capability_key=?",
            (str(name), str(description), mid, str(kind), str(gate_ref),
             str(why), str(probe_tool), key))
        action = "updated"
    else:
        conn.execute(
            "INSERT INTO capability_registry (capability_key, name, "
            "description, module_id, capability_kind, gate_ref, why, "
            "probe_tool) VALUES (?,?,?,?,?,?,?,?)",
            (key, str(name), str(description), mid, str(kind), str(gate_ref),
             str(why), str(probe_tool)))
        action = "added"
    if tools is not None:
        set_tools(conn, key, tools, probe_safe_tools=probe_safe_tools)
    conn.commit()
    return {"ok": True, "action": action, "capability_key": key,
            "kind": str(kind), "module_key": str(module_key),
            "tools": len(tools or ())}


def set_tools(conn: sqlite3.Connection, capability_key: str,
              tools: list[str] | tuple[str, ...], *,
              probe_safe_tools: list[str] | tuple[str, ...] | None = None,
              replace: bool = True) -> dict[str, Any]:
    """Declare which tools provide a capability.

    `replace=True` (the default) makes the call DECLARATIVE: the tools given
    become the whole set. `replace=False` adds without removing, for a tool
    discovered later.

    `probe_safe_tools` defaults to NONE SAFE. That is the direction that fails
    safe: a tool is unsafe until someone proves it read-only, which is the rule
    the user stated — "the default answer for an unproven tool is do not touch
    it".
    """
    ensure_schema(conn)
    row = conn.execute("SELECT capability_id FROM capability_registry WHERE "
                       "capability_key=?", (str(capability_key),)).fetchone()
    if not row:
        raise CapabilityRefused(
            ["capability %r is not registered, so its tools have nowhere to "
             "hang. Register the capability first." % capability_key])
    cid = int(row[0])
    safe = {str(t) for t in (probe_safe_tools or ())}
    if replace:
        conn.execute("DELETE FROM capability_tool WHERE capability_id=?",
                     (cid,))
    added = 0
    for i, t in enumerate(tools or (), start=1):
        name = str(t or "").strip()
        if not name:
            continue
        cur = conn.execute(
            "INSERT INTO capability_tool (capability_id, tool_name, "
            "is_probe_safe, sort_order) VALUES (?,?,?,?) "
            "ON CONFLICT (capability_id, tool_name) DO UPDATE SET "
            "is_probe_safe=excluded.is_probe_safe, "
            "sort_order=excluded.sort_order, updated_at=datetime('now')",
            (cid, name, int(name in safe), i))
        added += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "capability_key": str(capability_key),
            "tools": len(tools or ()), "probe_safe": len(safe),
            "written": added}


def tools_for(conn: sqlite3.Connection, capability_key: str) -> list[dict]:
    """The tools that provide a capability, in declared order."""
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT t.tool_name, t.is_probe_safe, t.sort_order "
        "FROM capability_tool t "
        "JOIN capability_registry c ON c.capability_id = t.capability_id "
        "WHERE c.capability_key=? ORDER BY t.sort_order, t.tool_name",
        (str(capability_key),))]


def probe_safe_tools(conn: sqlite3.Connection) -> set[str]:
    """EVERY tool proven read-only, across all capabilities.

    This is the ALLOWLIST the health probe must consult. It is read from the
    table, so a new safe tool is a ROW, not a code change.
    """
    ensure_schema(conn)
    return {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT tool_name FROM capability_tool WHERE is_probe_safe=1")}


def capabilities(conn: sqlite3.Connection, *,
                 module_key: str | None = None,
                 kind: str | None = None) -> list[dict[str, Any]]:
    """The ACTIVE capabilities, read from the table (never hand-listed).

    Returns the DECLARATION only. `state` / `in_server` / `note` are
    OBSERVATIONS and are computed by the caller — storing them would make a
    stale reading look like a fact.
    """
    ensure_schema(conn)
    where, params = ["c.is_active=1"], []
    if module_key:
        where.append("m.module_key=?")
        params.append(str(module_key))
    if kind:
        where.append("c.capability_kind=?")
        params.append(str(kind))
    return [dict(r) for r in conn.execute(
        "SELECT c.capability_id, c.capability_key, c.name, c.description, "
        "       c.capability_kind, c.gate_ref, c.why, c.probe_tool, "
        "       m.module_key "
        "FROM capability_registry c "
        "JOIN module_registry m ON m.module_id = c.module_id "
        "WHERE %s ORDER BY c.capability_key" % " AND ".join(where),
        tuple(params))]


def validate_kinds(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit EVERY capability row against the kind vocabulary.

    A gate that only guards its own front door is not a gate: a row inserted by
    a migration or a repair script was never checked. This audits the TABLE.
    """
    known = set(kinds(conn))
    bad = [{"capability_key": str(r[0]), "capability_kind": str(r[1])}
           for r in conn.execute(
               "SELECT capability_key, capability_kind FROM "
               "capability_registry WHERE is_active=1")
           if str(r[1]) not in known]
    return {"ok": not bad, "bad": bad, "known_kinds": sorted(known)}


# ---------------------------------------------------------------------------
# THE SEEDS. A seed populates an empty registry; it is never authoritative.
# ---------------------------------------------------------------------------
# OpenClaw's abilities, taken from `openclaw_settings.CAPABILITIES` — the list
# that used to be the ONLY record of them.
#
# MIGRATED 2026-09-21. The literal held 9 entries; this seed held 6. The
# migration had to ADD the 4 it was missing (Canvas, App control, Chat,
# Location) and RESOLVE one disagreement: the literal MERGED screen capture and
# screen record into one entry, while the DB and
# `llm_service_store.MCP_TOOL_FOR_CAPABILITY` BOTH split them. The DB's split is
# kept (it is more precise, and the UI change is VISIBLE rather than silent).
#
# `probe_tool='NA'` means "deliberately NOT probed", because calling it would
# capture the screen / play audio / run a command. That is the user's rule: the
# default answer for an unproven tool is do not touch it.
OPENCLAW_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"capability_key": "openclaw.screen_capture", "name": "Screen Capture",
     "kind": "eye",
     "description": "Captures the screen (screen.snapshot). THE EYE.",
     "why": "evidence screenshots + vision checks",
     "gate_ref": "NodeScreenEnabled + ScreenRecordingConsentGiven",
     "probe_tool": "NA",
     "tools": ("screen.snapshot",)},
    {"capability_key": "openclaw.screen_record", "name": "Screen Record",
     "kind": "eye",
     "description": "Records the screen (screen.record).",
     "why": "screen recording for evidence clips",
     "gate_ref": "NodeScreenEnabled + ScreenRecordingConsentGiven",
     "probe_tool": "NA",
     "tools": ("screen.record",)},
    {"capability_key": "openclaw.camera", "name": "Camera",
     "kind": "eye",
     "description": "Takes a photo or records video (camera.snap / camera.clip).",
     "why": "photo / video capture",
     "gate_ref": "NA",
     "probe_tool": "camera.list",
     "tools": ("camera.list", "camera.snap", "camera.clip"),
     "probe_safe": ("camera.list",)},
    {"capability_key": "openclaw.system_exec", "name": "System Exec",
     "kind": "hand",
     "description": "Runs a shell command (system.run). THE HAND.",
     "why": "run shell commands from an agent",
     "gate_ref": "SystemRunAllowOutbound / SystemRunAllowWindowsUi",
     "probe_tool": "system.which",
     "tools": ("system.run", "system.run.prepare", "system.which"),
     "probe_safe": ("system.which",)},
    {"capability_key": "openclaw.notify", "name": "Notify",
     "kind": "voice",
     "description": "Shows a desktop notification (system.notify).",
     "why": "desktop notifications",
     "gate_ref": "NA",
     "probe_tool": "NA",
     "tools": ("system.notify",)},
    {"capability_key": "openclaw.speech", "name": "Speech",
     "kind": "voice",
     "description": "Text to speech / speech to text (tts.speak / stt.listen).",
     "why": "text to speech / speech to text",
     "gate_ref": "NA",
     "probe_tool": "tts.status",
     "tools": ("tts.speak", "tts.status", "stt.listen", "stt.transcribe"),
     "probe_safe": ("tts.status", "stt.status"),},
    # ---- the 4 the seed was MISSING (present only in the literal) ----
    # Q2 NOT ANSWERED by the user; the kinds below are the recommended defaults,
    # recorded here so the choice is visible and reversible.
    {"capability_key": "openclaw.canvas_render", "name": "Canvas Render",
     "kind": "hand",
     "description": "Agent-rendered panels (canvas.present / canvas.navigate / "
                    "canvas.eval). SPLIT from the literal's single 'Canvas' "
                    "entry, because eval ACTS while snapshot only READS.",
     "why": "agent-rendered panels",
     "gate_ref": "NA",
     "probe_tool": "canvas.caps",
     "tools": ("canvas.present", "canvas.navigate", "canvas.eval")},
    {"capability_key": "openclaw.canvas_snapshot", "name": "Canvas Snapshot",
     "kind": "eye",
     "description": "Reads a rendered canvas (canvas.snapshot / canvas.caps).",
     "why": "read back an agent-rendered panel",
     "gate_ref": "NA",
     "probe_tool": "canvas.caps",
     "tools": ("canvas.snapshot", "canvas.caps"),
     "probe_safe": ("canvas.caps",)},
    {"capability_key": "openclaw.app_control", "name": "App Control",
     "kind": "hand",
     "description": "Drives the OpenClaw app itself (app.navigate / app.search "
                    "/ app.menu / app.sessions).",
     "why": "drive the OpenClaw app itself",
     "gate_ref": "NA",
     "probe_tool": "app.status",
     "tools": ("app.navigate", "app.search", "app.menu", "app.sessions"),
     "probe_safe": ("app.search", "app.menu", "app.sessions",
                    "app.status", "app.config.get", "app.settings.get",
                    "app.dashboard.url", "app.nodes", "app.agents"),},
    {"capability_key": "openclaw.chat", "name": "Chat",
     "kind": "hand",
     "description": "Sends into an OpenClaw chat session (app.chat.send / "
                    "app.chat.snapshot / app.chat.reset).",
     "why": "send into an OpenClaw chat session",
     "gate_ref": "NA",
     "probe_tool": "app.chat.snapshot",
     "tools": ("app.chat.send", "app.chat.snapshot", "app.chat.reset"),
     "probe_safe": ("app.chat.snapshot", "app.chat.queue.list"),},
    {"capability_key": "openclaw.location", "name": "Location",
     "kind": "eye",
     "description": "Geo lookup (location.get).",
     "why": "geo lookup",
     "gate_ref": "NA",
     "probe_tool": "location.get",
     "tools": ("location.get",),
     "probe_safe": ("location.get", "device.info", "device.status",
                    "ollama.models", "app.connection.status",
                    "app.connection.gateways",
                    "app.connection.pendingApprovals"),},
    # THE READ-ONLY STATE TOOLS. DEFECT FOUND BY MEASURING IT: 9 of the 23 tools
    # `openclaw_settings.PROBE_SAFE_TOOLS` proved read-only belonged to NO
    # capability, so they could not be declared in `capability_tool` at all. A
    # safe tool with no capability is a tool the registry cannot describe — and
    # the ALLOWLIST would then have to stay a Python set forever.
    # Reading device / app / runtime state IS a capability (kind `eye`: it
    # reports state and changes nothing), so it gets its own row.
    {"capability_key": "openclaw.device_state", "name": "Device & App State",
     "kind": "eye",
     "description": "Reads device, app, connection and local-runtime state and "
                    "changes NOTHING. These are the tools a health probe may "
                    "call: device.info / app.status / ollama.models / "
                    "app.connection.status. THE READ-ONLY STATE READ.",
     "why": "health probe: report state without touching anything",
     "gate_ref": "NA",
     "probe_tool": "device.info",
     "tools": ("device.info", "device.status", "app.status",
               "app.config.get", "app.settings.get", "app.dashboard.url",
               "app.nodes", "app.agents", "app.connection.status",
               "app.connection.gateways", "app.connection.pendingApprovals",
               "app.chat.queue.list", "stt.status", "ollama.models"),
     "probe_safe": ("device.info", "device.status", "app.status",
                    "app.config.get", "app.settings.get", "app.dashboard.url",
                    "app.nodes", "app.agents", "app.connection.status",
                    "app.connection.gateways",
                    "app.connection.pendingApprovals",
                    "app.chat.queue.list", "stt.status", "ollama.models"),},
)

# The LLM's abilities. `thinking` is the kind the user named.
LLM_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"capability_key": "llm.text_completion", "name": "Text Completion",
     "kind": "thinking",
     "description": "Reads text and returns a judgement. THE THINKING.",
     "why": "text reasoning served by an LLM",
     "gate_ref": "NA",
     "probe_tool": "NA",
     "tools": ()},
    {"capability_key": "llm.vision_analyze", "name": "Vision Analyze",
     "kind": "thinking",
     "description": "Reads an image and returns a judgement. The thinking half "
                    "of seeing: OpenClaw captures, the LLM interprets.",
     "why": "vision reasoning served by an LLM",
     "gate_ref": "NA",
     "probe_tool": "NA",
     "tools": ()},
)


def seed_capabilities(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the LLM module + both providers' capabilities. Idempotent.

    The tools are seeded too, because a capability whose tools live in a Python
    literal cannot gain a tool without a code change — the exact defect this
    migration removes.
    """
    ensure_schema(conn)
    mod = ensure_module(conn, LLM_MODULE_KEY, "LLM Runtime",
                        description="The local/remote LLM runtime (ollama). "
                                    "Provides THINKING.")
    added, kept, n_tools = 0, 0, 0
    for module_key, specs in ((OPENCLAW_MODULE_KEY, OPENCLAW_CAPABILITIES),
                             (LLM_MODULE_KEY, LLM_CAPABILITIES)):
        for spec in specs:
            try:
                r = register(conn, spec["capability_key"], spec["name"],
                             module_key=module_key, kind=spec["kind"],
                             description=spec.get("description", ""),
                             gate_ref=spec.get("gate_ref", "NA"),
                             why=spec.get("why", "NA"),
                             probe_tool=spec.get("probe_tool", "NA"),
                             tools=spec.get("tools") or (),
                             probe_safe_tools=spec.get("probe_safe") or ())
                added += int(r["action"] == "added")
                kept += int(r["action"] == "updated")
                n_tools += r.get("tools", 0)
            except CapabilityRefused:
                # ALREADY REGISTERED. `register()` correctly refuses to
                # overwrite it — but the TOOLS must still be declared, or a
                # legacy capability would keep its tools in a Python literal
                # forever. DEFECT FOUND BY RUNNING IT: the first version of this
                # seed counted the refusal and moved on, so 12 of the 25 tools
                # were never written to `capability_tool`.
                kept += 1
                try:
                    set_tools(conn, spec["capability_key"],
                              spec.get("tools") or (),
                              probe_safe_tools=spec.get("probe_safe") or ())
                    n_tools += len(spec.get("tools") or ())
                except CapabilityRefused:
                    pass
    return {"ok": True, "llm_module": mod, "added": added, "kept": kept,
            "tools_declared": n_tools,
            "openclaw": len(capabilities(conn, module_key=OPENCLAW_MODULE_KEY)),
            "llm": len(capabilities(conn, module_key=LLM_MODULE_KEY))}


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Capability registry (DB-driven)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--kind", default="")
    ap.add_argument("--module", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.seed:
            print(json.dumps(seed_capabilities(conn), indent=2))
            return 0
        if args.validate:
            out = validate_kinds(conn)
            print(json.dumps(out, indent=2) if args.json else
                  "ok=%s bad=%d" % (out["ok"], len(out["bad"])))
            return 0 if out["ok"] else 1
        rows = capabilities(conn, module_key=args.module or None,
                            kind=args.kind or None)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print("%-28s %-9s %s" % (r["capability_key"],
                                         r["capability_kind"], r["module_key"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
