"""OpenClaw Companion Local MCP — register_id + invoke trace spine.

Law (same as managed coding):
  no register_id → not managed code
  MCP / notify / snapshot = telemetry only (gate=never)
  trace write must never block host flow

Flow:
  seed registers under openclaw_companion / cap-1.0 / oc.mcp-tools
  → production call sites use traced_* wrappers
  → managed_invoke → function_invoker → function_invoke_trace
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

PIPELINE_ID = "D_openclaw_mcp_trace"
GATE_POLICY = "never"

MODULE_CODE = "openclaw_companion"
SYSTEM_KEY = "openclaw"
VERSION_LABEL = "cap-1.0"
ROOT_LABEL = "oc.mcp-tools"
CHANNEL_CODE = "local_pc"
SOURCE_SEED = "openclaw_mcp_seed"
SOURCE_INVOKE = "openclaw_mcp_trace"

# Stable function_name values (do not random-allocate on re-seed)
TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "label": "oc.1",
        "key": "screen_snapshot",
        "function_name": "mcp.screen_snapshot",
        "mcp_tool": "screen.snapshot",
        "title": "MCP screen.snapshot",
        "file_path": "mcp_client.py",
        "args_default": {"screenIndex": 0, "maxWidth": 1280},
        "sort": 1,
    },
    {
        "label": "oc.2",
        "key": "system_notify",
        "function_name": "mcp.system_notify",
        "mcp_tool": "system.notify",
        "title": "MCP system.notify",
        "file_path": "mcp_client.py",
        "args_default": {"title": "str", "body": "str"},
        "sort": 2,
    },
    {
        "label": "oc.3",
        "key": "ping",
        "function_name": "mcp.ping",
        "mcp_tool": "ping",
        "title": "MCP ping (list/presence)",
        "file_path": "mcp_client.py",
        "args_default": {},
        "sort": 3,
    },
    {
        "label": "oc.4",
        "key": "webbrowser_open",
        "function_name": "fallback.webbrowser_open",
        "mcp_tool": None,
        "title": "Fallback webbrowser.open (not MCP)",
        "file_path": "schema_qc.py",
        "args_default": {"url": "str"},
        "sort": 4,
    },
)

ENROLL_SOURCES = tuple(
    f"{MODULE_CODE}.{s['function_name']}" for s in TOOL_SPECS
)


def contracts_doc() -> dict[str, Any]:
    return {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "module": MODULE_CODE,
        "system_key": SYSTEM_KEY,
        "version": VERSION_LABEL,
        "root_label": ROOT_LABEL,
        "tools": [
            {
                "label": s["label"],
                "key": s["key"],
                "function_name": s["function_name"],
                "mcp_tool": s["mcp_tool"],
            }
            for s in TOOL_SPECS
        ],
        "enroll_sources": list(ENROLL_SOURCES),
        "law": [
            "register_id required for managed MCP path",
            "trace is telemetry only — never structure gate",
            "missing DB/register → still call MCP (soft)",
        ],
        "call_sites": [
            "openclaw_bridge.try_live_snapshot",
            "openclaw_bridge.try_notify",
            "schema_qc.try_mcp_shot",
            "schema_qc.try_notify",
            "schema_qc.open_browser_best_effort",
        ],
    }


def _get_or_create_dim(conn: sqlite3.Connection, table: str, code: str, name: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        f"INSERT INTO {table} (code, name) VALUES (?, ?)",
        (code, name),
    )
    return int(cur.lastrowid)


def _ensure_action(
    conn: sqlite3.Connection,
    *,
    element: str,
    action: str,
    code: str,
    name: str,
) -> int:
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO task_action_name (element, action, code, name, requires_tdd, status)
        VALUES (?, ?, ?, ?, 0, 'active')
        """,
        (element, action, code, name),
    )
    return int(cur.lastrowid)


def list_openclaw_registers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT register_id, module_name, function_name, slice_key, status,
               task_id, system_key, file_path, line_start, line_end
        FROM code_register
        WHERE module_name = ? AND system_key = ?
        ORDER BY id
        """,
        (MODULE_CODE, SYSTEM_KEY),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "register_id": r[0],
                "module_name": r[1],
                "function_name": r[2],
                "slice_key": r[3],
                "status": r[4],
                "task_id": r[5],
                "system_key": r[6],
                "file_path": r[7] if len(r) > 7 else None,
                "line_start": r[8] if len(r) > 8 else None,
                "line_end": r[9] if len(r) > 9 else None,
            }
        )
    return out


def get_register_for_tool(
    conn: sqlite3.Connection,
    *,
    function_name: str | None = None,
    slice_key: str | None = None,
) -> dict[str, Any] | None:
    from managed_coding import get_code_register

    if function_name:
        reg = get_code_register(
            conn, module_name=MODULE_CODE, function_name=function_name
        )
        if reg:
            return reg
    if slice_key:
        row = conn.execute(
            """
            SELECT register_id FROM code_register
            WHERE module_name = ? AND system_key = ? AND slice_key = ?
              AND status IN ('active', 'draft', 'zombie')
            ORDER BY id LIMIT 1
            """,
            (MODULE_CODE, SYSTEM_KEY, slice_key),
        ).fetchone()
        if row:
            return get_code_register(conn, register_id=str(row[0]))
    return None


def seed_openclaw_mcp_registers(
    conn: sqlite3.Connection,
    *,
    commit: bool = True,
    register_functions: bool = True,
) -> dict[str, Any]:
    """Idempotent: oc.mcp-tools root + tool slices + code_register + onto bindings."""
    from db_schema import upsert_task_ssot
    from managed_coding import (
        apply_slice_profile_dims,
        bind_onto,
        link_onto,
        register_managed_function,
        upsert_onto_concept,
    )

    out: dict[str, Any] = {
        "system_key": SYSTEM_KEY,
        "module": MODULE_CODE,
        "version": VERSION_LABEL,
        "created_tasks": 0,
        "slices": [],
        "registers": [],
        "onto": [],
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
    }

    channel_id = _get_or_create_dim(conn, "channel", CHANNEL_CODE, "Local PC")
    module_id = _get_or_create_dim(
        conn, "module", MODULE_CODE, "OpenClaw Companion"
    )
    act_sys = _ensure_action(
        conn,
        element="capability",
        action="ssot",
        code="capability.ssot",
        name="Capability multi-dim SSOT (tools/MCP/API)",
    )
    act_slice = _ensure_action(
        conn,
        element="slice",
        action="managed",
        code="slice.managed",
        name="Managed field slice",
    )

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, VERSION_LABEL),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                channel_id,
                module_id,
                VERSION_LABEL,
                "OpenClaw capability SSOT",
                "MCP tool register_id spine; gate=never",
            ),
        )
        version_id = int(cur.lastrowid)

    root_payload = {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "system_key": SYSTEM_KEY,
        "goal_type": "implement_capability",
        "goal_text": "Trace OpenClaw Companion Local MCP tools via register_id",
        "tool_vendor": "openclaw",
        "tools": [s["mcp_tool"] or s["function_name"] for s in TOOL_SPECS],
    }

    root = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, ROOT_LABEL),
    ).fetchone()
    if root:
        root_id = int(root[0])
        conn.execute(
            """
            UPDATE dev_task
            SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "OpenClaw MCP tools (register_id spine)",
                json.dumps(root_payload, ensure_ascii=False),
                module_id,
                channel_id,
                root_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                channel_id,
                module_id,
                act_sys,
                version_id,
                ROOT_LABEL,
                "OpenClaw MCP tools (register_id spine)",
                json.dumps(root_payload, ensure_ascii=False),
            ),
        )
        root_id = int(cur.lastrowid)
        out["created_tasks"] += 1

    root_dims = [
        (10, "system.key", SYSTEM_KEY, "string"),
        (20, "tool.vendor", "openclaw", "string"),
        (30, "tool.mcp_supported", "yes", "bool"),
        (
            40,
            "tool.mcp_tools",
            json.dumps(
                [s["mcp_tool"] for s in TOOL_SPECS if s.get("mcp_tool")],
                ensure_ascii=False,
            ),
            "json",
        ),
        (50, "coding.law", "register_id_required", "string"),
        (60, "gate", GATE_POLICY, "string"),
        (70, "pipeline", PIPELINE_ID, "string"),
    ]
    for sort_order, dim_key, value_text, value_type in root_dims:
        upsert_task_ssot(
            conn,
            task_id=root_id,
            dim_key=dim_key,
            value_text=value_text,
            value_type=value_type,
            source=SOURCE_SEED,
            sort_order=sort_order,
            notes="openclaw mcp tools root",
            commit=False,
        )

    # Ontology
    sys_code = f"sys.{SYSTEM_KEY}"
    ch_code = f"ch.{CHANNEL_CODE}"
    mod_code = f"mod.{MODULE_CODE}"
    out["onto"].append(
        upsert_onto_concept(
            conn,
            code=sys_code,
            kind="system",
            title="OpenClaw",
            notes="Companion Local MCP control plane",
            commit=False,
        )
    )
    out["onto"].append(
        upsert_onto_concept(
            conn, code=ch_code, kind="channel", title="Local PC", commit=False
        )
    )
    out["onto"].append(
        upsert_onto_concept(
            conn,
            code=mod_code,
            kind="module",
            title="OpenClaw Companion",
            commit=False,
        )
    )
    link_onto(conn, from_code=sys_code, to_code=ch_code, rel="on_channel", commit=False)
    link_onto(conn, from_code=sys_code, to_code=mod_code, rel="uses_module", commit=False)
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="task",
        bind_key=ROOT_LABEL,
        bind_id=root_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="version",
        bind_key=VERSION_LABEL,
        bind_id=version_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=mod_code,
        bind_type="module",
        bind_key=MODULE_CODE,
        bind_id=module_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=ch_code,
        bind_type="channel",
        bind_key=CHANNEL_CODE,
        bind_id=channel_id,
        commit=False,
    )

    register_map: dict[str, str] = {}

    for spec in TOOL_SPECS:
        label = str(spec["label"])
        skey = str(spec["key"])
        fn_name = str(spec["function_name"])
        existing = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, label),
        ).fetchone()
        slice_payload = {
            "pipeline": PIPELINE_ID,
            "system_key": SYSTEM_KEY,
            "slice_key": skey,
            "mcp_tool": spec.get("mcp_tool"),
            "function_name": fn_name,
            "parent_label": ROOT_LABEL,
            "table": "mcp_tool",
            "gate": GATE_POLICY,
        }
        if existing:
            slice_id = int(existing[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, parent_task_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(spec["title"]),
                    json.dumps(slice_payload, ensure_ascii=False),
                    root_id,
                    slice_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO dev_task
                    (parent_task_id, channel_id, module_id, action_name_id, version_id,
                     task_label, title, payload_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    root_id,
                    channel_id,
                    module_id,
                    act_slice,
                    version_id,
                    label,
                    str(spec["title"]),
                    json.dumps(slice_payload, ensure_ascii=False),
                ),
            )
            slice_id = int(cur.lastrowid)
            out["created_tasks"] += 1

        apply_slice_profile_dims(
            conn,
            task_id=slice_id,
            system_key=SYSTEM_KEY,
            slice_key=skey,
            table_name="mcp_tool",
            module_code=MODULE_CODE,
            tdd_code="json",
            api_path=f"mcp://{spec.get('mcp_tool') or fn_name}",
            function_name=fn_name,
            slice_status="active",
            source=SOURCE_SEED,
            tdd_rule={
                "tdd_type_code": "json",
                "rules": [],
                "args_default": spec.get("args_default") or {},
                "mcp_tool": spec.get("mcp_tool"),
            },
            commit=False,
        )
        upsert_task_ssot(
            conn,
            task_id=slice_id,
            dim_key="tool.mcp_name",
            value_text=str(spec.get("mcp_tool") or ""),
            value_type="string",
            source=SOURCE_SEED,
            sort_order=200,
            notes="live Companion tool name if any",
            commit=False,
        )
        upsert_task_ssot(
            conn,
            task_id=slice_id,
            dim_key="tool.args_default",
            value_text=json.dumps(spec.get("args_default") or {}, ensure_ascii=False),
            value_type="json",
            source=SOURCE_SEED,
            sort_order=210,
            commit=False,
        )

        slice_concept = f"slice.{SYSTEM_KEY}.{skey}"
        upsert_onto_concept(
            conn,
            code=slice_concept,
            kind="slice",
            title=f"{label} {skey}",
            commit=False,
        )
        link_onto(
            conn, from_code=sys_code, to_code=slice_concept, rel="has_slice", commit=False
        )
        bind_onto(
            conn,
            concept_code=slice_concept,
            bind_type="task",
            bind_key=label,
            bind_id=slice_id,
            commit=False,
        )

        reg_info = None
        if register_functions:
            existing_reg = conn.execute(
                """
                SELECT register_id, function_name, module_name
                FROM code_register
                WHERE module_name = ? AND function_name = ?
                LIMIT 1
                """,
                (MODULE_CODE, fn_name),
            ).fetchone()
            if not existing_reg:
                existing_reg = conn.execute(
                    """
                    SELECT register_id, function_name, module_name
                    FROM code_register
                    WHERE system_key = ? AND slice_key = ?
                      AND status IN ('active', 'draft', 'zombie')
                    ORDER BY id LIMIT 1
                    """,
                    (SYSTEM_KEY, skey),
                ).fetchone()

            fp = str(spec.get("file_path") or "mcp_client.py")
            reg_info = register_managed_function(
                conn,
                task_id=slice_id,
                tacid=label,
                module_name=MODULE_CODE,
                function_name=fn_name if not existing_reg else str(existing_reg[1]),
                system_task_id=root_id,
                slice_task_id=slice_id,
                system_key=SYSTEM_KEY,
                slice_key=skey,
                required=True,
                status="active",
                source=SOURCE_SEED,
                notes=str(spec["title"]),
                file_path=fp,
                line_start=1,
                commit=False,
            )
            apply_slice_profile_dims(
                conn,
                task_id=slice_id,
                system_key=SYSTEM_KEY,
                slice_key=skey,
                table_name="mcp_tool",
                module_code=MODULE_CODE,
                tdd_code="json",
                api_path=f"mcp://{spec.get('mcp_tool') or fn_name}",
                function_name=reg_info.get("function_name"),
                register_id=reg_info.get("register_id"),
                slice_status="active",
                source=SOURCE_SEED,
                tdd_rule={
                    "tdd_type_code": "json",
                    "rules": [],
                    "args_default": spec.get("args_default") or {},
                    "mcp_tool": spec.get("mcp_tool"),
                },
                commit=False,
            )
            rid = str(reg_info.get("register_id") or "")
            if rid:
                register_map[skey] = rid
                if spec.get("mcp_tool"):
                    register_map[str(spec["mcp_tool"])] = rid

            fn_concept = f"fn.{MODULE_CODE}.{reg_info.get('function_name')}"
            upsert_onto_concept(
                conn,
                code=fn_concept,
                kind="function",
                title=str(reg_info.get("function_name")),
                commit=False,
            )
            link_onto(
                conn,
                from_code=mod_code,
                to_code=fn_concept,
                rel="has_function",
                commit=False,
            )
            link_onto(
                conn,
                from_code=slice_concept,
                to_code=fn_concept,
                rel="implements",
                commit=False,
            )
            bind_onto(
                conn,
                concept_code=fn_concept,
                bind_type="register",
                bind_key=rid,
                bind_id=slice_id,
                commit=False,
            )
            if spec.get("mcp_tool"):
                bind_onto(
                    conn,
                    concept_code=fn_concept,
                    bind_type="other",
                    bind_key=f"mcp_tool:{spec['mcp_tool']}",
                    commit=False,
                )
            out["registers"].append(
                {
                    "register_id": rid,
                    "function_name": reg_info.get("function_name"),
                    "tacid": label,
                    "slice_key": skey,
                    "mcp_tool": spec.get("mcp_tool"),
                }
            )

        out["slices"].append(
            {
                "task_id": slice_id,
                "task_label": label,
                "slice_key": skey,
                "register_id": (reg_info or {}).get("register_id"),
                "function_name": (reg_info or {}).get("function_name") or fn_name,
                "mcp_tool": spec.get("mcp_tool"),
            }
        )

    upsert_task_ssot(
        conn,
        task_id=root_id,
        dim_key="tool.register_ids",
        value_text=json.dumps(register_map, ensure_ascii=False),
        value_type="json",
        source=SOURCE_SEED,
        sort_order=80,
        notes="slice_key / mcp tool name → register_id",
        commit=False,
    )

    out["root_task_id"] = root_id
    out["version_id"] = version_id
    out["module_id"] = module_id
    out["channel_id"] = channel_id
    out["register_map"] = register_map

    if commit:
        conn.commit()
    return out


def _open_conn(db_path: str | None = None) -> tuple[sqlite3.Connection, bool]:
    from db_schema import get_db_path

    path = get_db_path(db_path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn, True


def _traced_run(
    *,
    function_name: str,
    slice_key: str,
    fn: Any,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    tacid: str | None = None,
    task_id: int | None = None,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
    require_register: bool = False,
) -> dict[str, Any]:
    """Run callable via managed_invoke when possible; soft-fallback to bare call.

    require_register=False so missing seed never blocks MCP host paths.
    """
    from managed_coding import managed_invoke

    own = conn is None
    if own:
        try:
            conn, own = _open_conn(db_path)
        except Exception as e:
            # No DB — bare call
            try:
                result = fn(*(args or ()), **(kwargs or {}))
                return {
                    "ok": True,
                    "result": result,
                    "traced": False,
                    "error": None,
                    "trace_skip": f"db_open:{type(e).__name__}",
                    "gate": GATE_POLICY,
                }
            except Exception as e2:
                return {
                    "ok": False,
                    "result": None,
                    "traced": False,
                    "error": f"{type(e2).__name__}:{e2}",
                    "gate": GATE_POLICY,
                }

    assert conn is not None
    try:
        # Ensure seed once (idempotent) if register missing
        reg = get_register_for_tool(conn, function_name=function_name, slice_key=slice_key)
        if reg is None:
            try:
                seed_openclaw_mcp_registers(conn, commit=True, register_functions=True)
                reg = get_register_for_tool(
                    conn, function_name=function_name, slice_key=slice_key
                )
            except Exception:
                reg = None

        rid = (reg or {}).get("register_id")
        tac = tacid or (reg or {}).get("tacid") or f"oc.{slice_key}"
        tid = task_id if task_id is not None else (reg or {}).get("task_id")

        try:
            inv = managed_invoke(
                fn,
                register_id=str(rid) if rid else None,
                module_name=MODULE_CODE,
                function_name=function_name,
                tacid=str(tac),
                task_id=int(tid) if tid is not None else None,
                require_register=require_register and rid is not None,
                conn=conn,
                args=args,
                kwargs=kwargs or {},
                reraise=False,
                commit=True,
                source=SOURCE_INVOKE,
            )
        except Exception as e:
            # Last resort bare call
            try:
                result = fn(*(args or ()), **(kwargs or {}))
                return {
                    "ok": True,
                    "result": result,
                    "traced": False,
                    "error": None,
                    "trace_skip": f"managed_invoke:{type(e).__name__}",
                    "register_id": rid,
                    "gate": GATE_POLICY,
                }
            except Exception as e2:
                return {
                    "ok": False,
                    "result": None,
                    "traced": False,
                    "error": f"{type(e2).__name__}:{e2}",
                    "register_id": rid,
                    "gate": GATE_POLICY,
                }

        if not isinstance(inv, dict):
            inv = {"ok": bool(inv), "result": inv}
        # If refused unregistered, bare-call once
        if inv.get("error") == "unregistered_function":
            try:
                result = fn(*(args or ()), **(kwargs or {}))
                inv = {
                    "ok": True,
                    "result": result,
                    "traced": False,
                    "error": None,
                    "trace_skip": "unregistered_function",
                    "gate": GATE_POLICY,
                }
            except Exception as e2:
                inv = {
                    "ok": False,
                    "result": None,
                    "traced": False,
                    "error": f"{type(e2).__name__}:{e2}",
                    "gate": GATE_POLICY,
                }
        else:
            inv["traced"] = True
        inv.setdefault("register_id", rid)
        inv.setdefault("gate", GATE_POLICY)
        inv["function_name"] = function_name
        inv["module_name"] = MODULE_CODE
        inv["slice_key"] = slice_key
        return inv
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


def traced_screen_snapshot(
    arguments: dict[str, Any] | None = None,
    *,
    tacid: str | None = None,
    task_id: int | None = None,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    def _call() -> Any:
        from mcp_client import McpClient, McpConfig

        client = McpClient(McpConfig.from_env())
        return client.screen_snapshot(arguments)

    return _traced_run(
        function_name="mcp.screen_snapshot",
        slice_key="screen_snapshot",
        fn=_call,
        tacid=tacid,
        task_id=task_id,
        db_path=db_path,
        conn=conn,
    )


def traced_notify(
    title: str,
    body: str,
    *,
    tacid: str | None = None,
    task_id: int | None = None,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    def _call() -> Any:
        from mcp_client import McpClient, McpConfig

        client = McpClient(McpConfig.from_env())
        return client.notify(title, body)

    return _traced_run(
        function_name="mcp.system_notify",
        slice_key="system_notify",
        fn=_call,
        tacid=tacid,
        task_id=task_id,
        db_path=db_path,
        conn=conn,
    )


def traced_ping(
    *,
    tacid: str | None = None,
    task_id: int | None = None,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    def _call() -> Any:
        from mcp_client import McpClient, McpConfig

        client = McpClient(McpConfig.from_env())
        names = client.list_tool_names()
        return {"tools": names, "count": len(names)}

    return _traced_run(
        function_name="mcp.ping",
        slice_key="ping",
        fn=_call,
        tacid=tacid,
        task_id=task_id,
        db_path=db_path,
        conn=conn,
    )


def traced_webbrowser_open(
    url: str,
    *,
    delay: float = 0.0,
    tacid: str | None = None,
    task_id: int | None = None,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    def _call() -> Any:
        webbrowser.open(url)
        if delay and delay > 0:
            time.sleep(float(delay))
        return {"url": url, "opened": True}

    return _traced_run(
        function_name="fallback.webbrowser_open",
        slice_key="webbrowser_open",
        fn=_call,
        tacid=tacid,
        task_id=task_id,
        db_path=db_path,
        conn=conn,
    )


def sync_live_tools(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Compare live tools/list vs seeded tool.mcp_tools. Detect only (gate=never)."""
    from db_schema import upsert_task_ssot

    own = conn is None
    if own:
        conn, own = _open_conn(db_path)
    assert conn is not None

    live: list[str] = []
    live_err: str | None = None
    try:
        from mcp_client import McpClient, McpConfig

        client = McpClient(McpConfig.from_env())
        live = list(client.list_tool_names())
    except Exception as e:
        live_err = f"{type(e).__name__}:{e}"

    # Find root task
    row = conn.execute(
        """
        SELECT t.id FROM dev_task t
        JOIN version_center v ON v.id = t.version_id
        JOIN module m ON m.id = t.module_id
        WHERE t.task_label = ? AND m.code = ? AND v.version_label = ?
        ORDER BY t.id DESC LIMIT 1
        """,
        (ROOT_LABEL, MODULE_CODE, VERSION_LABEL),
    ).fetchone()
    root_id = int(row[0]) if row else None

    seeded = [s["mcp_tool"] for s in TOOL_SPECS if s.get("mcp_tool")]
    live_set = {x.lower() for x in live}
    missing_in_live = [t for t in seeded if t and t.lower() not in live_set]
    extra_in_live = [
        t
        for t in live
        if t.lower() not in {x.lower() for x in seeded if x}
    ]

    if root_id is not None:
        upsert_task_ssot(
            conn,
            task_id=root_id,
            dim_key="tool.mcp_tools.live",
            value_text=json.dumps(live, ensure_ascii=False),
            value_type="json",
            source="openclaw_mcp_sync",
            sort_order=90,
            notes=live_err or "live tools/list",
            commit=False,
        )
        upsert_task_ssot(
            conn,
            task_id=root_id,
            dim_key="tool.mcp_tools.drift",
            value_text=json.dumps(
                {
                    "missing_in_live": missing_in_live,
                    "extra_in_live": extra_in_live,
                    "live_error": live_err,
                },
                ensure_ascii=False,
            ),
            value_type="json",
            source="openclaw_mcp_sync",
            sort_order=91,
            commit=False,
        )
        if commit:
            conn.commit()

    if own:
        conn.close()

    return {
        "ok": live_err is None,
        "gate": GATE_POLICY,
        "live_tools": live,
        "seeded_tools": seeded,
        "missing_in_live": missing_in_live,
        "extra_in_live": extra_in_live,
        "live_error": live_err,
        "root_task_id": root_id,
        "detect_only": True,
    }


def selftest(db_path: str | None = None) -> dict[str, Any]:
    """Seed + mock traced invoke without live Companion."""
    from db_schema import get_db_path

    path = get_db_path(db_path)
    # Prefer quiet path: open DB and ensure task-center tables via seed helpers.
    # Full ensure_schema() is noisy; migrate separately via create_db.py --migrate.
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        try:
            from db_schema import ensure_task_center_schema

            ensure_task_center_schema(conn)
            conn.commit()
        except Exception:
            pass
        seed = seed_openclaw_mcp_registers(conn, commit=True)
        regs = list_openclaw_registers(conn)
        by_fn = {r["function_name"]: r for r in regs}

        # Mock notify path through managed_invoke without MCP
        def _fake_notify() -> str:
            return "ok-notify"

        inv = _traced_run(
            function_name="mcp.system_notify",
            slice_key="system_notify",
            fn=_fake_notify,
            conn=conn,
            tacid="oc.selftest",
        )

        # Count traces for module
        n_trace = conn.execute(
            """
            SELECT COUNT(*) FROM function_invoke_trace
            WHERE module_name = ?
            """,
            (MODULE_CODE,),
        ).fetchone()
        trace_n = int(n_trace[0]) if n_trace else 0

        need_fns = [s["function_name"] for s in TOOL_SPECS]
        missing = [f for f in need_fns if f not in by_fn]
        ok = (
            len(missing) == 0
            and bool(inv.get("ok"))
            and bool(inv.get("register_id") or inv.get("traced") is not None)
        )
        return {
            "ok": ok,
            "gate": GATE_POLICY,
            "registers": [
                {
                    "function_name": f,
                    "register_id": (by_fn.get(f) or {}).get("register_id"),
                }
                for f in need_fns
            ],
            "missing_functions": missing,
            "seed_registers": len(seed.get("registers") or []),
            "invoke_ok": bool(inv.get("ok")),
            "invoke_traced": inv.get("traced"),
            "invoke_register_id": inv.get("register_id"),
            "trace_rows_module": trace_n,
            "root_task_id": seed.get("root_task_id"),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "usage: openclaw_mcp_trace.py [contracts|seed|selftest|list|sync|notify TITLE BODY]"
        )
        return 0
    cmd = args[0].strip().lower()
    if cmd == "contracts":
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "seed":
        from db_schema import ensure_schema, get_db_path

        ensure_schema()
        conn = sqlite3.connect(get_db_path())
        try:
            out = seed_openclaw_mcp_registers(conn, commit=True)
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        finally:
            conn.close()
        return 0
    if cmd == "selftest":
        out = selftest()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if cmd == "list":
        from db_schema import ensure_schema, get_db_path

        ensure_schema()
        conn = sqlite3.connect(get_db_path())
        try:
            print(
                json.dumps(
                    list_openclaw_registers(conn),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        finally:
            conn.close()
        return 0
    if cmd == "sync":
        out = sync_live_tools()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if cmd == "notify" and len(args) >= 3:
        title, body = args[1], " ".join(args[2:])
        out = traced_notify(title, body)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1
    if cmd == "snapshot":
        out = traced_screen_snapshot()
        # Don't dump huge base64
        if isinstance(out.get("result"), (dict, list, str)):
            out = dict(out)
            out["result"] = f"<{type(out.get('result')).__name__}>"
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
