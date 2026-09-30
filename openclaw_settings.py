"""OpenClaw settings + capability probe.

Reads the OpenClawTray config from disk AND probes the live MCP server so the UI
can show, in one place: what the server exposes, which tools actually WORK right
now, and the on-disk settings that gate them.

WHY both: `screen.snapshot` returned "Capture failed" while settings.json said
`NodeScreenEnabled: true` and `ScreenRecordingConsentGiven: true`. Only
cross-reading config + tool list + live probe exposes that contradiction. A page
showing only the file, or only the tools, would have hidden it.

READ-ONLY. There is no set_setting: changing security settings is a user action,
not an agent action.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

# Where OpenClawTray keeps its config.
SETTINGS_CANDIDATES = [
    Path(os.environ.get("APPDATA", "")) / "OpenClawTray" / "settings.json",
    Path(os.environ.get("LOCALAPPDATA", "")) / "OpenClawTray" / "settings.json",
]

# Where the tray app itself lives. Discovered from the uninstall registry key
# ("OpenClaw Companion", InstallLocation ...\OpenClawTray\) and confirmed on
# disk; the env override exists so a different install can be pointed at
# without a code change.
OPENCLAW_EXE_CANDIDATES = [
    Path(os.environ.get("OPENCLAW_EXE", "")) if os.environ.get("OPENCLAW_EXE") else None,
    Path(os.environ.get("LOCALAPPDATA", "")) / "OpenClawTray" / "OpenClaw.Tray.WinUI.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "OpenClawTray" / "OpenClawTray.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "OpenClawTray" / "OpenClaw.Tray.WinUI.exe",
]
OPENCLAW_PROCESS_NAMES = ("OpenClaw.Tray.WinUI", "OpenClawTray", "OpenClaw Companion")
# How long to wait for the MCP port to accept a connection after launching.
LAUNCH_WAIT_SEC = 25.0
LAUNCH_POLL_SEC = 1.0

# Settings that gate a capability. Grouped, because a flat list of 30 keys hides
# which one explains a failure.
KEY_GROUPS: dict[str, list[str]] = {
    "screen": [
        "NodeScreenEnabled",
        "ScreenRecordingConsentGiven",
        "CaptureConsentTimeoutMs",
    ],
    "mcp": ["EnableMcpServer"],
    "exec": [
        "SystemRunAllowOutbound",
        "SystemRunAllowWindowsUi",
    ],
}

# Tools worth probing. Probe order = report order.
#
# HARD RULE: a health probe must be SIDE-EFFECT-FREE and CHEAP.
# `screen.snapshot` was in this list and it is neither: it captures the whole
# screen (1920x1080, ~300-450 KB) and raises a Windows "OpenClaw agent is
# capturing your screen" notification. Because the report is polled, that turned
# a status check into repeated screen recording — 597 captures in the log, in
# bursts of 7 within one second. Never probe a capture/record tool.
PROBE_TOOLS = [
    ("device.info", {}),
    ("device.status", {}),
    ("app.status", {}),
    ("ollama.models", {}),
]

# Tools that must NEVER be probed automatically, with the reason. Kept as data
# so the rule is enforced rather than remembered.
FORBIDDEN_PROBE_TOOLS = {
    "screen.snapshot": "captures the full screen + raises a capture notification",
    "screen.record": "records the screen",
    "camera.snap": "takes a photo",
    "camera.clip": "records video",
    "tts.speak": "plays audio out loud",
    "stt.listen": "opens the microphone",
    "system.notify": "shows a desktop notification",
    "system.run": "executes a shell command",
}

# Keyword -> reason. Used to flag a side-effecting tool even when it is not on
# the explicit list above, so a NEW capture/record tool is caught automatically
# instead of silently becoming a "health check".
#
# PRECISION MATTERS: a warning that fires on harmless tools is as useless as no
# warning. A first attempt matched generic verbs ("run", "type", "write",
# "send") against the DESCRIPTION and flagged 32 of 59 tools — including
# `tts.status` and `ollama.chat`, which are read-only. So the scan is split:
#
#   STRONG  — unambiguous, matched against name AND description.
#   NAME    — weaker verbs, matched against the tool NAME only, because the same
#             words appear harmlessly in prose ("runtime", "type: string").
SIDE_EFFECT_KEYWORDS = (
    ("capture", "captures the screen"),
    ("screenshot", "captures the screen"),
    ("screen.record", "records the screen"),
    ("camera.snap", "takes a photo"),
    ("camera.clip", "records video"),
    ("tts.speak", "plays audio out loud"),
    ("stt.listen", "opens the microphone"),
    ("stt.transcribe", "opens the microphone"),
    ("system.notify", "shows a desktop notification"),
    ("system.run", "executes a shell command"),
    ("system.exec", "executes a command"),
    ("keystroke", "sends keystrokes"),
)

SIDE_EFFECT_NAME_KEYWORDS = (
    ("send", "sends a message on the user's behalf"),
    ("write", "writes data"),
    ("delete", "destroys data"),
    ("cancel", "cancels an operation"),
    ("apply", "applies a change"),
    ("navigate", "drives another application"),
    ("push", "pushes data to a target"),
    ("click", "moves the mouse / clicks"),
)

# Tools that are safe to auto-probe even though a NAME keyword matches. Kept
# explicit so the exception is visible rather than a silent gap.
PROBE_SAFE_EXCEPTIONS = {
    "app.status",
    "device.info",
    "device.status",
    "ollama.models",
}

# THE ALLOWLIST. A health probe may call ONLY these tools.
#
# DEFECT FOUND BY MEASURING IT (2026-09-21): the classification used to be a
# BLACKLIST — `FORBIDDEN_PROBE_TOOLS` named 8 of 59 tools, then keyword-scanned
# the name and description. Measured: 38 tools were marked `probe_safe` with NO
# read-only proof, including `app.settings.set` (persists a setting),
# `app.chat.reset` (resets a session), `canvas.eval` (executes JavaScript),
# `browser.proxy` (proxies HTTP to the CDP host), `ollama.chat` (sends a prompt),
# and five `app.connection.*` tools that approve/reject pairings or reconnect a
# gateway. The keyword lists lacked `set`, `reset`, `eval`, `proxy`, `chat`,
# `connect`, `approve`, `reject`, `reconnect`, so none was caught.
#
# The user's rule: "一個 health probe 嘅預設答案應該係「唔好掂」，而唔係「應該
# 冇事」" — the default answer for an unproven tool is DO NOT TOUCH IT, not "it is
# probably fine". A blacklist cannot keep up: it named 8 of 59, and the same
# family already produced 597 screen captures. An ALLOWLIST fails SAFE — a new
# tool is unsafe until someone proves otherwise.
#
# Every entry below is READ-ONLY: it reports state and changes nothing.
PROBE_SAFE_TOOLS = {
    # device / app state
    "device.info",
    "device.status",
    "app.status",
    "app.config.get",
    "app.settings.get",
    "app.dashboard.url",
    "app.nodes",
    "app.sessions",
    "app.agents",
    "app.menu",
    "app.search",
    # connection state (read-only: status/list, NOT connect/approve/reject)
    "app.connection.status",
    "app.connection.gateways",
    "app.connection.pendingApprovals",
    # chat state (read-only: list/snapshot, NOT reset/send)
    "app.chat.queue.list",
    "app.chat.snapshot",
    # local runtimes
    "ollama.models",
    "stt.status",
    "tts.status",
    "system.which",
    # hardware inventory
    "camera.list",
    "canvas.caps",
    "location.get",
}

# THE ALLOWLIST IS NOW A TABLE. `PROBE_SAFE_TOOLS` above is the SEED that
# populates `capability_tool.is_probe_safe`, so a new safe tool is a ROW rather
# than a code change — the same demotion already applied to the capability list
# and to `CAPABILITY_TAGS`. The set is kept as the fallback and as the record of
# what was proven by hand.
def probe_safe_tools(conn: Any = None) -> set[str]:
    """EVERY tool proven read-only. The DB is the source; the seed is fallback."""
    try:
        import capability_store as cs

        if conn is None:
            import sqlite3

            conn = sqlite3.connect(str(Path(__file__).resolve().parent /
                                        "agent.db"))
            own = True
        else:
            own = False
        try:
            found = cs.probe_safe_tools(conn)
            return found or set(PROBE_SAFE_TOOLS)
        finally:
            if own:
                conn.close()
    except Exception:
        return set(PROBE_SAFE_TOOLS)

# Capability view: what an operator cares about, mapped to the tools providing it.
# A raw tool list does not answer "can I take a screenshot?".
#
# ============================================================================
# THIS IS A SEED, NOT THE SOURCE OF TRUTH. (demoted 2026-09-21)
# ============================================================================
# DEFECT FOUND BY MEASURING IT: the capability -> tool mapping existed in FOUR
# places and they DISAGREED.
#
#   1. this literal                          9 capabilities, screen MERGED
#   2. `capability_registry` + `capability_tool` (the DB)   the source now
#   3. `llm_service_store.MCP_TOOL_FOR_CAPABILITY`          a FALLBACK now
#   4. `openclaw_mcp_trace.TOOL_SPECS`        a trace spine, a different purpose
#
# Copies 2 and 3 BOTH split screen into capture + record; this literal MERGED
# them. So "read the DB instead of the literal" would have silently MERGED two
# capabilities and DROPPED four (Canvas, App control, Chat, Location).
#
# The read path is now `capability_store.capabilities(module_key=
# 'openclaw_companion')` + `capability_store.tools_for()`. This tuple is kept
# ONLY as the seed that populates an empty registry, and as the record of what
# the literal used to say. Editing it changes NOTHING at runtime.
#
# `probe` names the tool used to test the capability, or None when testing it
# would have a side effect (see FORBIDDEN_PROBE_TOOLS). A capability with
# probe=None is reported as "present" when its tools are in the server's tool
# list — it is NEVER reported as "failing", because we deliberately did not
# call it. Reporting "failing" for an untested capability would be a false
# negative, the mirror of the false-OK bug.
CAPABILITIES = [
    {"name": "Screen capture", "tools": ["screen.snapshot", "screen.record"],
     "probe": None,
     "gate": "NodeScreenEnabled + ScreenRecordingConsentGiven",
     "why": "evidence screenshots + vision checks"},
    {"name": "Camera", "tools": ["camera.list", "camera.snap", "camera.clip"],
     "probe": None,
     "gate": "-", "why": "photo / video capture"},
    {"name": "System exec",
     "tools": ["system.run", "system.run.prepare", "system.which"],
     "probe": None,
     "gate": "SystemRunAllowOutbound / SystemRunAllowWindowsUi",
     "why": "run shell commands from an agent"},
    {"name": "Notify", "tools": ["system.notify"], "probe": None, "gate": "-",
     "why": "desktop notifications"},
    {"name": "Speech",
     "tools": ["tts.speak", "tts.status", "stt.listen", "stt.transcribe"],
     "probe": None,
     "gate": "-", "why": "text to speech / speech to text"},
    {"name": "Canvas",
     "tools": ["canvas.present", "canvas.navigate", "canvas.snapshot", "canvas.eval"],
     "probe": None,
     "gate": "-", "why": "agent-rendered panels"},
    {"name": "App control",
     "tools": ["app.navigate", "app.search", "app.menu", "app.sessions"],
     "probe": None,
     "gate": "-", "why": "drive the OpenClaw app itself"},
    {"name": "Chat",
     "tools": ["app.chat.send", "app.chat.snapshot", "app.chat.reset"],
     "probe": None,
     "gate": "-", "why": "send into an OpenClaw chat session"},
    {"name": "Location", "tools": ["location.get"], "probe": None, "gate": "-",
     "why": "geo lookup"},
]

# Settings keys that matter when a capability is failing.
GATE_KEYS = (
    "NodeScreenEnabled",
    "ScreenRecordingConsentGiven",
    "EnableMcpServer",
    "SystemRunAllowOutbound",
    "SystemRunAllowWindowsUi",
)


def settings_path() -> Path | None:
    for p in SETTINGS_CANDIDATES:
        if p.is_file():
            return p
    return None


def read_settings() -> dict[str, Any]:
    """Read settings.json. Never raises."""
    p = settings_path()
    out: dict[str, Any] = {
        "ok": True,
        "path": str(p) if p else None,
        "found": bool(p),
        "raw": {},
        "groups": {},
        "key_count": 0,
        "error": None,
    }
    if not p:
        out["ok"] = False
        out["error"] = "settings.json not found in %s" % (
            " | ".join(str(x) for x in SETTINGS_CANDIDATES))
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        out["raw"] = data if isinstance(data, dict) else {}
    except Exception as e:
        out["ok"] = False
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    for gname, keys in KEY_GROUPS.items():
        out["groups"][gname] = {k: out["raw"].get(k, "<unset>") for k in keys}
    out["key_count"] = len(out["raw"])
    return out


def _parse_args_prose(description: str) -> list[dict[str, Any]]:
    """Parse the `Args: name (type, required), ...` convention.

    WHY: this MCP server returns an EMPTY inputSchema for all 59 tools and puts
    the parameters in the description prose instead. Rendering "Parameters (0)"
    from the schema alone is technically correct and completely useless — the
    operator still cannot see what a tool needs. So the prose is parsed.

    Example input:
      "Capture a screenshot. Args: format ('png'|'jpeg', default 'png'),
       maxWidth (int, default 1920), includePointer (bool, default true).
       Returns { ... }"
    """
    if not description:
        return []
    m = re.search(r"\bArgs:\s*(.+?)(?:\.\s*(?:Returns|Only|Note)\b|$)", description, re.S)
    if not m:
        return []
    body = m.group(1)
    params: list[dict[str, Any]] = []
    # name (spec) — name may contain " / " aliases and dots.
    for pm in re.finditer(r"([A-Za-z_][\w./ ]*?)\s*\(([^)]*)\)", body):
        raw_name = pm.group(1).strip().rstrip(",").strip()
        spec = pm.group(2).strip()
        if not raw_name:
            continue
        # "monitor / screenIndex" -> primary name + aliases
        parts = [p.strip() for p in raw_name.split("/") if p.strip()]
        name = parts[0]
        aliases = parts[1:]
        low = spec.lower()
        # type = leading token before the first comma
        ptype = spec.split(",")[0].strip() or "any"
        required = "required" in low and "optional" not in low
        default = None
        dm = re.search(r"default\s+([^,)]+)", spec, re.I)
        if dm:
            default = dm.group(1).strip()
        # description = the remainder after the type, minus default/required noise
        desc = spec
        if "," in spec:
            desc = spec.split(",", 1)[1].strip()
        desc = re.sub(r"\b(default|required|optional)\b[^,]*", "", desc, flags=re.I)
        desc = desc.strip(" ,;")
        params.append({
            "name": name,
            "aliases": aliases,
            "type": ptype,
            "description": desc,
            "required": required,
            "default": default,
            "enum": None,
            "source": "description prose",
        })
    return params


def classify_tool(name: str, description: str = "", *,
                  safe_tools: set[str] | None = None) -> dict[str, Any]:
    """Classify ONE tool: is it a side effect, and is it proven probe-safe?

    PURE — it takes a NAME and a DESCRIPTION and touches no network. This is
    extracted from `tool_details()` (2026-09-23) because the RULE and the
    TRANSPORT were fused: `tool_details()` opens an MCP session, so the proof
    that "`probe_safe` is an allowlist" could only run while a live MCP server
    answered. MEASURED: with the server down, `tool_details()` returned
    `ok=False, tools=[]` and 15 checks failed — the proof was measuring the
    server's availability, not the rule. A rule that can only be checked when a
    server is up is not a rule you can rely on.

    The classification is unchanged; only its addressability is. `tool_details()`
    now calls this, so the proof and the page read the SAME function.

    `safe_tools` overrides the allowlist (defaults to `probe_safe_tools()`), so a
    caller can measure the rule against a fixed set without a DB.
    """
    reason = FORBIDDEN_PROBE_TOOLS.get(name)
    if not reason and name not in PROBE_SAFE_EXCEPTIONS:
        lname = name.lower()
        for kw, why in SIDE_EFFECT_KEYWORDS:
            if kw in lname:
                reason = why
                break
        if not reason:
            for kw, why in SIDE_EFFECT_NAME_KEYWORDS:
                if kw in lname:
                    reason = why
                    break
        if not reason:
            blob = ("%s %s" % (name, description or "")).lower()
            for kw, why in SIDE_EFFECT_KEYWORDS:
                if kw in blob:
                    reason = why
                    break
    # THE ALLOWLIST GATE. `probe_safe` is TRUE only when the tool is on the
    # proven read-only list. The keyword scan above still runs, because it
    # supplies the REASON shown to an operator — but it can no longer GRANT
    # safety. A tool the scan misses is unsafe by default, which is the
    # direction that fails safe.
    proven_safe = name in (probe_safe_tools() if safe_tools is None
                           else safe_tools)
    if not proven_safe and not reason:
        reason = ("not proven read-only; the default answer for an unproven "
                  "tool is do not touch it")
    return {
        "side_effect": bool(reason),
        "side_effect_reason": reason or "",
        "probe_safe": proven_safe,
    }


def tool_details() -> dict[str, Any]:
    """FULL detail for every MCP tool: description, input schema, annotations.

    WHY: the page used to render only tool NAMES as chips, throwing away the
    description and the complete JSON Schema that tools/list already returns.
    That made the checklist useless — you could see `screen.snapshot` exists but
    not that calling it captures your screen.

    Returns {ok, tools: [{name, description, input_schema, params, required,
    annotations, side_effect, side_effect_reason}], error}.
    """
    out: dict[str, Any] = {"ok": True, "tools": [], "error": None}
    try:
        import mcp_client

        client = mcp_client.McpClient(mcp_client.McpConfig.from_env())
        client.ensure_ready()
        raw = client._tools or {}
    except Exception as e:
        out["ok"] = False
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out

    rows: list[dict[str, Any]] = []
    for name in sorted(raw.keys()):
        t = raw.get(name) or {}
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        params = []
        for pname, pspec in props.items():
            pspec = pspec if isinstance(pspec, dict) else {}
            params.append({
                "name": pname,
                "aliases": [],
                "type": pspec.get("type") or ("enum" if pspec.get("enum") else "any"),
                "description": pspec.get("description") or "",
                "required": pname in required,
                "default": pspec.get("default"),
                "enum": pspec.get("enum"),
                "source": "inputSchema",
            })
        # This server ships an empty inputSchema and documents args in prose.
        # Fall back to parsing the description so the operator still sees them.
        if not params:
            params = _parse_args_prose(t.get("description") or "")
        # THE CLASSIFICATION IS A PURE FUNCTION (2026-09-23). It used to be
        # inline here, which fused the RULE with the TRANSPORT: this function
        # opens an MCP session, so the rule could only be checked while a server
        # answered. `classify_tool` is the same logic, addressable without a
        # network, and the proof reads it directly.
        cls = classify_tool(name, t.get("description") or "")
        rows.append({
            "name": name,
            "description": (t.get("description") or "").strip(),
            "input_schema": schema,
            "params": params,
            "required": required,
            "annotations": t.get("annotations") or {},
            "side_effect": cls["side_effect"],
            "side_effect_reason": cls["side_effect_reason"],
            "probe_safe": cls["probe_safe"],
        })
    out["tools"] = rows
    out["count"] = len(rows)
    return out


def probe_tools() -> dict[str, Any]:
    """Call a few key tools and report which work. Never raises."""
    out: dict[str, Any] = {"ok": True, "results": [], "tools": [],
                           "tool_count": 0, "error": None}
    try:
        import mcp_client
        client = mcp_client.McpClient(mcp_client.McpConfig.from_env())
    except Exception as e:
        out["ok"] = False
        out["error"] = "mcp_client unavailable: %s: %s" % (type(e).__name__, e)
        return out

    try:
        # refresh=True: a cached tool list would report a dead server as OK.
        names = client.list_tool_names(refresh=True)
        out["tools"] = names
        out["tool_count"] = len(names)
    except Exception as e:
        out["ok"] = False
        out["error"] = "tools/list failed: %s: %s" % (type(e).__name__, e)

    # If tools/list could not connect, every call_tool would fail with the SAME
    # connection error after its own timeout. Probing them serially turned a
    # dead server into a ~9s page stall for four identical messages. Skip them.
    if not out["ok"]:
        for tool, _args in PROBE_TOOLS:
            out["results"].append({
                "tool": tool, "ok": False, "isError": True, "ms": None,
                "detail": "skipped: MCP server unreachable (see tools/list error)",
            })
        return out

    # Probes are independent calls; run them concurrently. Each one is a network
    # round-trip, so serial execution scales linearly with PROBE_TOOLS.
    def _probe(item: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        tool, args = item
        entry: dict[str, Any] = {
            "tool": tool, "ok": False, "detail": None,
            "ms": None, "isError": True,
        }
        # Enforce the side-effect rule at the call site, not just in the list.
        if tool in FORBIDDEN_PROBE_TOOLS:
            entry["detail"] = (
                "refused: %s is not a safe health probe (%s)"
                % (tool, FORBIDDEN_PROBE_TOOLS[tool])
            )
            return entry
        t0 = time.time()
        try:
            res = client.call_tool(tool, args)
            entry["ms"] = int((time.time() - t0) * 1000)
            is_err = bool(isinstance(res, dict) and res.get("isError"))
            text = ""
            try:
                text = " ".join(
                    str(b.get("text") or "")
                    for b in (res.get("content") or [])
                    if isinstance(b, dict)
                )
            except Exception:
                text = str(res)
            entry["ok"] = not is_err
            entry["isError"] = is_err
            entry["detail"] = (text or "")[:300]
        except Exception as e:
            entry["ms"] = int((time.time() - t0) * 1000)
            entry["detail"] = "%s: %s" % (type(e).__name__, e)
            entry["isError"] = True
        return entry

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PROBE_TOOLS)) as pool:
        out["results"] = list(pool.map(_probe, PROBE_TOOLS))
    return out


def capability_declarations(conn: Any = None) -> list[dict[str, Any]]:
    """The capability DECLARATIONS, read from the DB (never hand-listed).

    Falls back to the `CAPABILITIES` SEED only when the DB cannot be read, and
    the fallback is REPORTED in `_source` rather than being silent — a fallback
    nobody notices is a second source of truth wearing a disguise.

    Returns rows shaped like the old literal: {name, tools, probe, gate, why}.
    """
    try:
        import capability_store as cs

        if conn is None:
            import sqlite3

            conn = sqlite3.connect(str(Path(__file__).resolve().parent /
                                        "agent.db"))
            conn.row_factory = sqlite3.Row
            own = True
        else:
            own = False
        try:
            rows = cs.capabilities(conn, module_key=cs.OPENCLAW_MODULE_KEY)
            if not rows:
                raise LookupError("no openclaw capabilities registered")
            out = []
            for r in rows:
                tools = cs.tools_for(conn, r["capability_key"])
                out.append({
                    "key": r["capability_key"],
                    "kind": r["capability_kind"],
                    "name": r["name"],
                    "tools": [t["tool_name"] for t in tools],
                    "probe": (None if str(r.get("probe_tool") or "NA") == "NA"
                              else r["probe_tool"]),
                    "gate": r.get("gate_ref") or "NA",
                    "why": r.get("why") or "NA",
                    "_source": "capability_registry",
                })
            return out
        finally:
            if own:
                conn.close()
    except Exception:
        # THE SEED FALLBACK. Reported, not silent.
        return [{
            "key": c["name"].lower().replace(" ", "_"),
            "kind": "NA", "name": c["name"], "tools": list(c["tools"]),
            "probe": c.get("probe"), "gate": c.get("gate", "NA"),
            "why": c.get("why", "NA"), "_source": "SEED (DB unreadable)",
        } for c in CAPABILITIES]


def capabilities(settings: dict[str, Any], probe: dict[str, Any], *,
                 conn: Any = None) -> list[dict[str, Any]]:
    """Per capability: working / failing / present / missing, plus why-not.

    THE DECLARATION / OBSERVATION SPLIT: the capability, its tools, its gate and
    its reason are DECLARATIONS and come from the DB. `state`, `in_server` and
    `note` are OBSERVATIONS — they are MEASURED here and never stored, because a
    stored reading becomes a fact the moment the world moves on.
    """
    raw = settings.get("raw") or {}
    present = set(probe.get("tools") or [])
    by_tool = {r["tool"]: r for r in (probe.get("results") or [])}
    decls = capability_declarations(conn)

    rows: list[dict[str, Any]] = []
    for cap in decls:
        tools = cap["tools"]
        in_server = [t for t in tools if t in present]
        probed = [by_tool[t] for t in tools if t in by_tool]
        # A capability whose probe is None was deliberately NOT called (calling
        # it would capture the screen / play audio / run a command). Report it
        # from the tool list only, and never as "failing" — we have no evidence
        # it fails, and claiming so would be a false negative.
        untested = cap.get("probe") is None
        if probed:
            state = "working" if any(p.get("ok") for p in probed) else "failing"
        elif in_server:
            state = "present"   # in tools/list but not probed
        else:
            state = "missing"

        note = ""
        if state == "failing":
            bad = [p for p in probed if not p.get("ok")]
            note = (bad[0].get("detail") or "")[:160]
            # A failing tool WITH its gate enabled is the case that matters:
            # config says yes, tool says no.
            for k in GATE_KEYS:
                if k in raw:
                    note += "  [%s=%s]" % (k, raw.get(k))
        elif untested and state == "present":
            note = "not probed on purpose (side effect); present in the tool list"
        rows.append({
            "key": cap.get("key", ""),
            "kind": cap.get("kind", "NA"),
            "source": cap.get("_source", "NA"),
            "name": cap["name"],
            "state": state,
            "tools": tools,
            "in_server": len(in_server),
            "gate": cap["gate"],
            "why": cap["why"],
            "note": note,
            "probed": not untested,
        })
    return rows


def find_openclaw_exe() -> str | None:
    """First existing OpenClawTray executable, or None."""
    for p in OPENCLAW_EXE_CANDIDATES:
        if p and p.is_file():
            return str(p)
    return None


def is_process_running() -> dict[str, Any]:
    """Is the tray process alive? Uses tasklist (no psutil dependency)."""
    out: dict[str, Any] = {"running": False, "name": None, "pid": None}
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10,
        )
        for line in (res.stdout or "").splitlines():
            parts = [c.strip('"') for c in line.split('","')]
            if not parts:
                continue
            exe = parts[0].strip('"')
            stem = exe[:-4] if exe.lower().endswith(".exe") else exe
            if stem.lower() in {n.lower() for n in OPENCLAW_PROCESS_NAMES}:
                out["running"] = True
                out["name"] = stem
                out["pid"] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                return out
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def port_open(host: str = "127.0.0.1", port: int = 8765, timeout: float = 1.5) -> bool:
    """True when something accepts a TCP connection on host:port."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _mcp_host_port() -> tuple[str, int]:
    """Host/port of the configured MCP endpoint (defaults 127.0.0.1:8765)."""
    from urllib.parse import urlparse

    url = (os.environ.get("OPENCLAW_MCP_URL") or "").strip()
    if url:
        try:
            u = urlparse(url)
            if u.hostname:
                return u.hostname, int(u.port or (443 if u.scheme == "https" else 80))
        except Exception:
            pass
    return "127.0.0.1", 8765


def launch_openclaw() -> dict[str, Any]:
    """Start the tray app detached. Never raises."""
    out: dict[str, Any] = {"launched": False, "exe": None, "error": None}
    exe = find_openclaw_exe()
    out["exe"] = exe
    if not exe:
        out["error"] = (
            "OpenClawTray executable not found. Set OPENCLAW_EXE or install "
            "OpenClaw Companion."
        )
        return out
    try:
        # DETACHED_PROCESS so the tray outlives this helper; CREATE_NO_WINDOW
        # would hide the tray icon, which the user needs to see.
        subprocess.Popen(
            [exe],
            cwd=str(Path(exe).parent),
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        out["launched"] = True
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def ensure_online(*, auto_connect: bool = True, wait_sec: float = LAUNCH_WAIT_SEC) -> dict[str, Any]:
    """Make the MCP server reachable, launching the app if needed.

    Returns {ok, action, waited_sec, exe, error}. `action` is one of:
      already_online | launched | launch_failed | not_installed | disabled
    """
    host, port = _mcp_host_port()
    out: dict[str, Any] = {
        "ok": False, "action": None, "waited_sec": 0.0,
        "exe": None, "error": None, "host": host, "port": port,
    }
    if port_open(host, port):
        out["ok"] = True
        out["action"] = "already_online"
        return out
    if not auto_connect:
        out["action"] = "disabled"
        out["error"] = "MCP port closed and auto-connect is disabled."
        return out
    exe = find_openclaw_exe()
    out["exe"] = exe
    if not exe:
        out["action"] = "not_installed"
        out["error"] = (
            "OpenClawTray executable not found. Set OPENCLAW_EXE or install "
            "OpenClaw Companion."
        )
        return out
    proc = is_process_running()
    if not proc.get("running"):
        res = launch_openclaw()
        if not res.get("launched"):
            out["action"] = "launch_failed"
            out["error"] = res.get("error")
            return out
    # Wait for the port. The tray needs a moment to start its MCP listener.
    t0 = time.time()
    while (time.time() - t0) < wait_sec:
        if port_open(host, port):
            out["ok"] = True
            out["action"] = "launched"
            out["waited_sec"] = round(time.time() - t0, 1)
            return out
        time.sleep(LAUNCH_POLL_SEC)
    out["waited_sec"] = round(time.time() - t0, 1)
    out["action"] = "launch_failed"
    out["error"] = (
        "Launched the tray but %s:%s did not open within %.0fs. Check that "
        "'Local MCP Server' is ON in OpenClaw Companion settings."
        % (host, port, wait_sec)
    )
    return out


def build_openclaw_report(*, auto_connect: bool = False,
                          probe: bool = False) -> dict[str, Any]:
    """Everything the OpenClaw settings page needs, in one call.

    auto_connect=True will launch the tray when the MCP port is closed. It is
    OFF by default so a read-only page view never starts a process as a side
    effect; the UI calls /api/openclaw/connect explicitly for that.

    `probe=False` BY DEFAULT (2026-09-23). MEASURED DEFECT: this used to ALWAYS
    call `probe_tools()` and `tool_details()`, and BOTH construct an `McpClient`
    and do a LIVE MCP round-trip. The UI polls `/api/openclaw/report` on a timer
    (`app.js:7351`), so a STATUS POLL opened an MCP session with OpenClaw
    Companion — and the Companion raises its "OpenClaw agent is capturing your
    screen" consent notification when a session starts. So the notification
    fired ON A TIMER with no capture ever requested.

    The user: "problem is middleware, 7B not = openclaw, 7B is working for task
    -> capability by the task / not call openclaw when 7B start".

    A STATUS READ MUST NOT OPEN AN MCP SESSION. The default report is now built
    from things that do NOT touch MCP:
      * `read_settings()`  — reads OpenClawTray's settings.json
      * `port_open()`      — a TCP connect check, not an MCP session
      * `is_process_running()` — a process check
    A caller that WANTS the live probe passes `probe=True` explicitly, and the
    report SAYS which it got (`probed`), so a silent probe is impossible.
    """
    s = read_settings()
    connect: dict[str, Any] | None = None
    if auto_connect:
        connect = ensure_online(auto_connect=True)
    # THE PROBE IS OPT-IN. `probe_tools()` / `tool_details()` open an MCP
    # session, so they run ONLY when the caller asked for them.
    if probe:
        p = probe_tools()
        details = tool_details()
    else:
        p = {"ok": True, "results": [], "tools": [], "tool_count": 0,
             "error": None, "probed": False,
             "note": ("not probed: a status read must not open an MCP session "
                      "(pass probe=True to probe)")}
        details = {"ok": True, "tools": [], "error": None, "probed": False}
    caps = capabilities(s, p)
    failing = [c["name"] for c in caps if c["state"] == "failing"]
    host, port = _mcp_host_port()
    return {
        "ok": True,
        "probed": bool(probe),
        "settings": s,
        "probe": p,
        "capabilities": caps,
        "connect": connect,
        "tool_details": details,
        "runtime": {
            "exe": find_openclaw_exe(),
            "process": is_process_running(),
            "port_open": port_open(host, port),
            "host": host,
            "port": port,
        },
        "summary": {
            "tool_count": p.get("tool_count", 0),
            "capabilities_working": sum(1 for c in caps if c["state"] == "working"),
            "capabilities_failing": len(failing),
            "failing": failing,
            "settings_found": s.get("found"),
            "probed": bool(probe),
        },
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# THE BACKGROUND REFRESHER — the work leaves the request path.
#
# THE HUMAN (2026-09-25):
#     "these fucking deign is wrong, it make the site become slow and slow"
#     "by trigger point before have the call!! not need tochecking status : alive"
#
# MEASURED, and this is the defect: `/api/system-status` called
# `check_openclaw_status()` on EVERY poll, and that built an `McpClient` and did
# a LIVE MCP round-trip. OpenClaw Companion is not running, so port 8765 refuses
# — and a REFUSED LOOPBACK CONNECT costs ~2040 ms on this machine:
#
#     2046.7 / 2041.7 / 2024.5 / 2037.3 ms   (four raw TCP connects)
#
# The UI polls every 5 s and the cache TTL was 8 s, so the cache was ALWAYS
# expired: MEASURED, 4 of 8 polls stalled 2-3.6 s (50%).
#
# WHY A THREAD AND NOT A SHORTER TTL: MEASURED, the check costs 1.5-7.4 s
# (`port_open` alone is 1510 ms; `build_openclaw_report(probe=False)` is
# 3966-7397 ms). NO TTL makes a 7 s call fast. The call must leave the request
# path entirely.
#
# WHY THE CACHE CARRIES ITS AGE: a cache that hides its age is the "recorded
# fact that was never measured" defect this repo keeps paying for. A reader must
# be able to tell "measured 3 s ago" from "never measured".
# ---------------------------------------------------------------------------

# The cadence. 30 s is chosen because the underlying check costs 1.5-7.4 s, so a
# faster cadence would spend a large fraction of a core on a probe whose answer
# changes on the scale of minutes.
OPENCLAW_REFRESH_SEC = 30.0

# The three Windows scheduled tasks this repo installs. MEASURED: they exist
# (`schtasks /Query`), and MEASURED: NO status endpoint read them, so the UI
# could not answer "are the keep-alive tasks alive?".
WINDOWS_TASK_NAMES = (
    "AgentSystemSkillTick",
    "AgentSystemWatchdogKeepAlive",
    "AgentSystemHeartbeatKeepAlive",
)

_STATUS_CACHE: dict[str, Any] = {
    "ts": 0.0,
    "openclaw": None,
    "windows_tasks": None,
    "refreshes": 0,
    "last_error": None,
}
_STATUS_LOCK = __import__("threading").Lock()
_REFRESHER_STARTED = False


def windows_tasks_status() -> dict[str, Any]:
    """The state of the three AgentSystem* Windows scheduled tasks.

    MEASURED: `install_skill_tick.py` / `install_watchdog_keepalive.py` create
    them with `schtasks`, and `_proof_skill_tick.py:71` already queries them with
    `schtasks /Query /TN`. ONE mechanism, already proven in this repo.

    This is a READ. It never creates, starts, stops, or deletes a task.
    """
    out: dict[str, Any] = {"ok": False, "tasks": [], "error": None}
    try:
        res = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
        if res.returncode != 0:
            out["error"] = "schtasks exit %s" % res.returncode
            return out
        wanted = {n.lower() for n in WINDOWS_TASK_NAMES}
        found: dict[str, dict[str, Any]] = {}
        for line in (res.stdout or "").splitlines():
            parts = [c.strip().strip('"') for c in line.split('","')]
            if not parts:
                continue
            name = parts[0].strip('"').lstrip("\\")
            if name.lower() not in wanted:
                continue
            # CSV columns: TaskName, Next Run Time, Status
            found[name.lower()] = {
                "name": name,
                "next_run": parts[1] if len(parts) > 1 else None,
                "status": parts[2] if len(parts) > 2 else None,
                "present": True,
            }
        for n in WINDOWS_TASK_NAMES:
            out["tasks"].append(
                found.get(n.lower())
                or {"name": n, "next_run": None, "status": None, "present": False}
            )
        out["ok"] = all(t["present"] for t in out["tasks"])
        out["present_count"] = sum(1 for t in out["tasks"] if t["present"])
        out["total"] = len(WINDOWS_TASK_NAMES)
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _refresh_once() -> None:
    """Do the REAL work once, into the cache. Never raises."""
    oc: dict[str, Any]
    try:
        oc = build_openclaw_report(probe=False)
    except Exception as e:  # noqa: BLE001
        oc = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    wt = windows_tasks_status()
    with _STATUS_LOCK:
        _STATUS_CACHE["ts"] = time.time()
        _STATUS_CACHE["openclaw"] = oc
        _STATUS_CACHE["windows_tasks"] = wt
        _STATUS_CACHE["refreshes"] = int(_STATUS_CACHE.get("refreshes") or 0) + 1
        _STATUS_CACHE["last_error"] = oc.get("error")


def _refresher_loop() -> None:
    while True:
        try:
            _refresh_once()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(OPENCLAW_REFRESH_SEC)


def start_status_refresher() -> dict[str, Any]:
    """Start the daemon refresher ONCE. Idempotent. Never raises."""
    global _REFRESHER_STARTED
    with _STATUS_LOCK:
        if _REFRESHER_STARTED:
            return {"started": False, "already": True}
        _REFRESHER_STARTED = True
    try:
        import threading

        threading.Thread(
            target=_refresher_loop, name="openclaw-status-refresher", daemon=True
        ).start()
        return {"started": True, "already": False, "interval_sec": OPENCLAW_REFRESH_SEC}
    except Exception as e:  # noqa: BLE001
        with _STATUS_LOCK:
            _REFRESHER_STARTED = False
        return {"started": False, "already": False,
                "error": "%s: %s" % (type(e).__name__, e)}


def status_cached() -> dict[str, Any]:
    """The cached status. NEVER blocks, NEVER does the work.

    Returns the payload PLUS `age_sec` and `stale`, so a reader can tell
    "measured 3 s ago" from "never measured". `stale` is True when the cache has
    never been filled OR is older than 3x the cadence — a cache that hides its
    age is forbidden.
    """
    with _STATUS_LOCK:
        ts = float(_STATUS_CACHE.get("ts") or 0.0)
        oc = _STATUS_CACHE.get("openclaw")
        wt = _STATUS_CACHE.get("windows_tasks")
        refreshes = int(_STATUS_CACHE.get("refreshes") or 0)
        last_error = _STATUS_CACHE.get("last_error")
    age = (time.time() - ts) if ts else None
    return {
        "openclaw": oc,
        "windows_tasks": wt,
        "age_sec": round(age, 1) if age is not None else None,
        "stale": (age is None) or (age > 3 * OPENCLAW_REFRESH_SEC),
        "refreshes": refreshes,
        "last_error": last_error,
        "interval_sec": OPENCLAW_REFRESH_SEC,
        "note": (
            "read from the background refresher's cache; the request path does "
            "NOT probe. POST /api/openclaw/refresh is the trigger point."
        ),
    }


def refresh_now() -> dict[str, Any]:
    """THE TRIGGER POINT. Do the real work NOW and return it.

    This is what a user action calls. The timer does NOT call it.
    """
    _refresh_once()
    return status_cached()


if __name__ == "__main__":
    print(json.dumps(build_openclaw_report(), indent=2, ensure_ascii=False,
                     default=str)[:4000])