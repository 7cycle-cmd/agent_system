"""Local agent.db browser + Task Center — stdlib only. Bind 127.0.0.1."""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import subprocess
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "agent.db"
HOST = "127.0.0.1"
PORT = 8766
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

try:
    from db_schema import (
        add_dev_task_field,
        create_dev_task,
        create_task_bundle,
        create_version,
        delete_task_ssot,
        ensure_schema,
        get_setting,
        list_fault_reports,
        list_task_ssot,
        update_dev_task_context,
        upsert_task_ssot,
    )
except Exception:
    ensure_schema = None
    create_version = None
    create_dev_task = None
    create_task_bundle = None
    add_dev_task_field = None
    list_task_ssot = None
    upsert_task_ssot = None
    delete_task_ssot = None
    list_fault_reports = None
    get_setting = None
    update_dev_task_context = None

try:
    from fail_handling import (
        run_fail_handling_supervisor,
        run_fail_report,
        run_fail_ssot_assemble,
        run_fail_vision_step2,
        run_fault_ssot_match,
        spawn_detached_fail_handling,
        spawn_pending_fail_handling,
    )
except Exception:
    run_fail_ssot_assemble = None
    run_fail_vision_step2 = None
    run_fail_report = None
    run_fault_ssot_match = None
    run_fail_handling_supervisor = None
    spawn_detached_fail_handling = None
    spawn_pending_fail_handling = None

try:
    from schema_qc import run_qc_for_task
except Exception:
    run_qc_for_task = None

try:
    from code_health import (
        generate_code_health_report,
        get_tacid_branch_function_report,
        harvest_static_impl_refs,
        spawn_dead_function_cleanup_tasks,
        verify_code_health_schema,
    )
except Exception:
    generate_code_health_report = None
    get_tacid_branch_function_report = None
    harvest_static_impl_refs = None
    spawn_dead_function_cleanup_tasks = None
    verify_code_health_schema = None

try:
    from managed_coding import (
        bind_register_source_location,
        builder_dashboard,
        list_field_tdd_rules,
        list_fn_requests,
        list_managed_systems,
        mark_demo_noise_rubbish,
        run_function_builder,
        seed_membership_system,
        verify_managed_schema,
        worker_clean_report,
    )
except Exception:
    worker_clean_report = None
    seed_membership_system = None
    mark_demo_noise_rubbish = None
    list_managed_systems = None
    verify_managed_schema = None
    run_function_builder = None
    builder_dashboard = None
    bind_register_source_location = None
    list_fn_requests = None
    list_field_tdd_rules = None

try:
    from pair_qc import (
        pair_qc_dashboard,
        run_pair_qc,
        verify_pair_qc_schema,
    )
except Exception:
    pair_qc_dashboard = None
    run_pair_qc = None
    verify_pair_qc_schema = None

try:
    from hko_weather_proof import (
        contracts_doc as hko_contracts_doc,
        hko_dashboard,
        load_latest as hko_load_latest,
        render_html_table as hko_render_html_table,
        run_proof as hko_run_proof,
        safe_proof_image as hko_safe_proof_image,
    )
except Exception:
    hko_contracts_doc = None
    hko_dashboard = None
    hko_load_latest = None
    hko_render_html_table = None
    hko_run_proof = None
    hko_safe_proof_image = None


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def list_tables(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def allowed_table(conn: sqlite3.Connection, name: str) -> bool:
    if not name or not str(name).replace("_", "").isalnum():
        return False
    return name in set(list_tables(conn))


def table_columns(conn: sqlite3.Connection, name: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")').fetchall()]


def fetch_table(conn: sqlite3.Connection, name: str, limit: int) -> dict:
    cols = table_columns(conn, name)
    order = ""
    if "event_id" in cols:
        order = " ORDER BY event_id DESC"
    elif "id" in cols:
        order = " ORDER BY id DESC"
    elif "created_at" in cols:
        order = " ORDER BY created_at DESC"
    sql = f'SELECT * FROM "{name}"{order} LIMIT ?'
    rows = conn.execute(sql, (limit,)).fetchall()
    count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    return {
        "table": name,
        "columns": cols,
        "rows": [list(r) for r in rows],
        "count": int(count),
        "limit": limit,
    }


def fetch_schema(conn: sqlite3.Connection, name: str) -> dict:
    """Authoritative structure via PRAGMA table_info (QC0)."""
    info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
    columns = []
    for r in info:
        columns.append(
            {
                "cid": r[0],
                "name": r[1],
                "type": r[2] or "",
                "notnull": bool(r[3]),
                "dflt_value": r[4],
                "pk": int(r[5] or 0),
            }
        )
    row_count = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    names = [c["name"] for c in columns]
    return {
        "table": name,
        "exists": True,
        "column_count": len(columns),
        "columns": columns,
        "column_names": names,
        "row_count": row_count,
        "browser_url": f"http://{HOST}:{PORT}/?table={name}&limit={DEFAULT_LIMIT}",
        "api_table_url": f"http://{HOST}:{PORT}/api/table/{name}?limit={DEFAULT_LIMIT}",
        "api_schema_url": f"http://{HOST}:{PORT}/api/schema/{name}",
    }


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def fetch_dims(conn: sqlite3.Connection) -> dict:
    """Catalog dims for Task Center filters/forms."""
    channels = [
        _row_to_dict(r)
        for r in conn.execute(
            "SELECT id, code, name FROM channel ORDER BY id"
        ).fetchall()
    ]
    modules = [
        _row_to_dict(r)
        for r in conn.execute(
            "SELECT id, code, name FROM module ORDER BY id"
        ).fetchall()
    ]
    actions = [
        _row_to_dict(r)
        for r in conn.execute(
            """
            SELECT id, element, action, code, name, requires_tdd, status
            FROM task_action_name ORDER BY id
            """
        ).fetchall()
    ]
    tdd_types = [
        _row_to_dict(r)
        for r in conn.execute(
            "SELECT id, code, name, sqlite_affinity FROM tdd_type ORDER BY id"
        ).fetchall()
    ]
    versions = [
        _row_to_dict(r)
        for r in conn.execute(
            """
            SELECT v.id, v.channel_id, v.module_id, v.version_label, v.title,
                   v.notes, v.status, v.parent_version_id,
                   c.code AS channel_code, m.code AS module_code
            FROM version_center v
            JOIN channel c ON c.id = v.channel_id
            JOIN module m ON m.id = v.module_id
            ORDER BY v.id
            """
        ).fetchall()
    ]
    return {
        "channels": channels,
        "modules": modules,
        "actions": actions,
        "tdd_types": tdd_types,
        "versions": versions,
    }


def fetch_task_rows(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    module_id: int | None = None,
    version_id: int | None = None,
    status: str | None = None,
) -> list[dict]:
    sql = """
        SELECT
            t.id, t.parent_task_id, t.channel_id, t.module_id,
            t.writer, t.session_id,
            t.action_name_id, t.version_id, t.task_label, t.title,
            t.payload_json, t.status, t.queue_task_id, t.qc_vision_id,
            t.created_at, t.updated_at, t.completed_at,
            c.code AS channel_code, c.name AS channel_name,
            m.code AS module_code, m.name AS module_name,
            a.code AS action_code, a.name AS action_name, a.requires_tdd,
            v.version_label, v.title AS version_title, v.status AS version_status
        FROM dev_task t
        JOIN channel c ON c.id = t.channel_id
        JOIN module m ON m.id = t.module_id
        JOIN task_action_name a ON a.id = t.action_name_id
        JOIN version_center v ON v.id = t.version_id
        WHERE 1=1
    """
    params: list = []
    if channel_id is not None:
        sql += " AND t.channel_id = ?"
        params.append(channel_id)
    if module_id is not None:
        sql += " AND t.module_id = ?"
        params.append(module_id)
    if version_id is not None:
        sql += " AND t.version_id = ?"
        params.append(version_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.version_id, t.parent_task_id IS NOT NULL, t.id"
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def build_task_tree(rows: list[dict]) -> list[dict]:
    """Nest tasks under channel → module → version → roots(+children)."""
    by_id = {int(r["id"]): dict(r, children=[]) for r in rows}
    roots_by_version: dict[int, list] = {}
    for r in rows:
        tid = int(r["id"])
        node = by_id[tid]
        pid = r.get("parent_task_id")
        if pid is not None and int(pid) in by_id:
            by_id[int(pid)]["children"].append(node)
        else:
            roots_by_version.setdefault(int(r["version_id"]), []).append(node)

    # group hierarchy
    channels: dict[int, dict] = {}
    for r in rows:
        cid = int(r["channel_id"])
        mid = int(r["module_id"])
        vid = int(r["version_id"])
        ch = channels.setdefault(
            cid,
            {
                "channel_id": cid,
                "channel_code": r["channel_code"],
                "channel_name": r["channel_name"],
                "modules": {},
            },
        )
        mod = ch["modules"].setdefault(
            mid,
            {
                "module_id": mid,
                "module_code": r["module_code"],
                "module_name": r["module_name"],
                "versions": {},
            },
        )
        if vid not in mod["versions"]:
            mod["versions"][vid] = {
                "version_id": vid,
                "version_label": r["version_label"],
                "version_title": r["version_title"],
                "version_status": r["version_status"],
                "tasks": roots_by_version.get(vid, []),
            }

    out = []
    for ch in channels.values():
        modules = []
        for mod in ch["modules"].values():
            modules.append(
                {
                    **{k: mod[k] for k in ("module_id", "module_code", "module_name")},
                    "versions": list(mod["versions"].values()),
                }
            )
        out.append(
            {
                "channel_id": ch["channel_id"],
                "channel_code": ch["channel_code"],
                "channel_name": ch["channel_name"],
                "modules": modules,
            }
        )
    return out


def fetch_task_tree(
    conn: sqlite3.Connection,
    *,
    channel_id: int | None = None,
    module_id: int | None = None,
    version_id: int | None = None,
    status: str | None = None,
) -> dict:
    rows = fetch_task_rows(
        conn,
        channel_id=channel_id,
        module_id=module_id,
        version_id=version_id,
        status=status,
    )
    return {"tasks": rows, "tree": build_task_tree(rows), "count": len(rows)}


def fetch_task_detail(conn: sqlite3.Connection, task_id: int) -> dict | None:
    rows = fetch_task_rows(conn)
    match = next((r for r in rows if int(r["id"]) == int(task_id)), None)
    if not match:
        return None
    children = [r for r in rows if r.get("parent_task_id") == int(task_id)]
    fields = [
        _row_to_dict(r)
        for r in conn.execute(
            """
            SELECT f.id, f.task_id, f.field_name, f.nullable, f.is_pk,
                   f.default_text, f.sort_order, f.tdd_type_id, f.action_name_id,
                   td.code AS tdd_code, td.name AS tdd_name, td.sqlite_affinity
            FROM dev_task_field f
            JOIN tdd_type td ON td.id = f.tdd_type_id
            WHERE f.task_id = ?
            ORDER BY f.sort_order, f.id
            """,
            (task_id,),
        ).fetchall()
    ]
    # also fields on children (field tasks)
    child_ids = [int(c["id"]) for c in children]
    child_fields = []
    if child_ids:
        placeholders = ",".join("?" * len(child_ids))
        child_fields = [
            _row_to_dict(r)
            for r in conn.execute(
                f"""
                SELECT f.id, f.task_id, f.field_name, f.nullable, f.is_pk,
                       f.default_text, f.sort_order, f.tdd_type_id,
                       td.code AS tdd_code, td.name AS tdd_name
                FROM dev_task_field f
                JOIN tdd_type td ON td.id = f.tdd_type_id
                WHERE f.task_id IN ({placeholders})
                ORDER BY f.task_id, f.sort_order, f.id
                """,
                child_ids,
            ).fetchall()
        ]
    qc_runs = [
        _row_to_dict(r)
        for r in conn.execute(
            """
            SELECT id, task_id, table_name, browser_url, api_ok, vision_id,
                   expected_json, observed_json, match_ok, summary, created_at
            FROM schema_qc_run
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (task_id,),
        ).fetchall()
    ]
    payload = {}
    if match.get("payload_json"):
        try:
            payload = json.loads(match["payload_json"])
        except Exception:
            payload = {}
    table_name = payload.get("table") if isinstance(payload, dict) else None
    ssot = []
    if table_name:
        ssot = [
            _row_to_dict(r)
            for r in conn.execute(
                """
                SELECT id, table_name, keyword, value_text, value_type, source,
                       task_id, version_id, updated_at
                FROM schema_ssot
                WHERE table_name = ?
                ORDER BY keyword
                """,
                (table_name,),
            ).fetchall()
        ]
    else:
        ssot = [
            _row_to_dict(r)
            for r in conn.execute(
                """
                SELECT id, table_name, keyword, value_text, value_type, source,
                       task_id, version_id, updated_at
                FROM schema_ssot
                WHERE task_id = ?
                ORDER BY keyword
                """,
                (task_id,),
            ).fetchall()
        ]

    browser_url = None
    api_schema_url = None
    if isinstance(payload, dict):
        browser_url = payload.get("browser_url")
        if table_name:
            if not browser_url:
                browser_url = (
                    f"http://{HOST}:{PORT}/?table={table_name}&limit={DEFAULT_LIMIT}"
                )
            api_schema_url = f"http://{HOST}:{PORT}/api/schema/{table_name}"

    goal = None
    if isinstance(payload, dict) and (
        payload.get("goal_type") or payload.get("goal_text")
    ):
        goal = {
            "goal_type": payload.get("goal_type"),
            "goal_text": payload.get("goal_text"),
            "success_criteria": payload.get("success_criteria"),
            "goal_source": payload.get("goal_source"),
            "plan_steps": payload.get("plan_steps") or [],
            "goal_context": payload.get("goal_context") or {},
            "pipeline": payload.get("pipeline"),
            "step1_std_rows": payload.get("step1_std_rows") or [],
            "step4_delta_rows": payload.get("step4_delta_rows") or [],
            "step4_summary": payload.get("step4_summary"),
            "step4_counts": payload.get("step4_counts") or {},
            "step2_std_rows": payload.get("step2_std_rows") or [],
            "step2_summary": payload.get("step2_summary"),
            "step2_model": payload.get("step2_model"),
            "step2_error": payload.get("step2_error"),
            "step2_vision_id": payload.get("step2_vision_id")
            or payload.get("vision_id"),
            "step2_image_path": payload.get("step2_image_path"),
            "step2_pending": payload.get("step2_pending"),
            "step5_report_id": payload.get("step5_report_id"),
            "step5_summary": payload.get("step5_summary"),
            "step5_std_rows": payload.get("step5_std_rows") or [],
            "step5_markdown": payload.get("step5_markdown"),
            "step5_top_deltas": payload.get("step5_top_deltas") or [],
            "step5_links": payload.get("step5_links") or {},
            "step5_notified_at": payload.get("step5_notified_at"),
            "step6_match_status": payload.get("step6_match_status"),
            "step6_match_score": payload.get("step6_match_score"),
            "step6_option_id": payload.get("step6_option_id"),
            "step6_option_code": payload.get("step6_option_code"),
            "step6_solution_id": payload.get("step6_solution_id"),
            "step6_summary": payload.get("step6_summary"),
            "step6_std_rows": payload.get("step6_std_rows") or [],
            "supervisor_summary": payload.get("supervisor_summary"),
            "supervisor_ran_at": payload.get("supervisor_ran_at"),
            "supervisor_results": payload.get("supervisor_results") or {},
        }

    task_ssot_rows: list = []
    if list_task_ssot is not None:
        try:
            task_ssot_rows = list_task_ssot(conn, int(task_id))
        except Exception:
            task_ssot_rows = []
    else:
        try:
            task_ssot_rows = [
                _row_to_dict(r)
                for r in conn.execute(
                    """
                    SELECT id, task_id, dim_key, value_text, value_type, source,
                           parent_dim_id, sort_order, notes, updated_at, created_at
                    FROM task_ssot
                    WHERE task_id = ?
                    ORDER BY sort_order, id
                    """,
                    (task_id,),
                ).fetchall()
            ]
        except sqlite3.Error:
            task_ssot_rows = []

    fault_reports: list = []
    case_id = None
    if isinstance(payload, dict) and payload.get("fault_event_id") is not None:
        try:
            case_id = int(payload["fault_event_id"])
        except Exception:
            case_id = None
    if list_fault_reports is not None:
        try:
            fault_reports = list_fault_reports(
                conn, event_id=case_id, remediate_task_id=int(task_id), limit=5
            )
        except Exception:
            fault_reports = []
    else:
        try:
            fault_reports = [
                _row_to_dict(r)
                for r in conn.execute(
                    """
                    SELECT id, event_id, remediate_task_id, summary, goal_type,
                           gate_result, created_at, notified_at
                    FROM fault_report
                    WHERE remediate_task_id = ?
                    ORDER BY id DESC LIMIT 5
                    """,
                    (task_id,),
                ).fetchall()
            ]
        except sqlite3.Error:
            fault_reports = []

    return {
        "task": match,
        "children": children,
        "fields": fields,
        "child_fields": child_fields,
        "qc_runs": qc_runs,
        "schema_ssot": ssot,
        "task_ssot": task_ssot_rows,
        "payload": payload,
        "goal": goal,
        "fault_reports": fault_reports,
        "table_name": table_name,
        "open_qc_url": browser_url,
        "open_schema_api_url": api_schema_url,
    }


SHARED_CSS = """
body{font-family:system-ui,sans-serif;margin:1.5rem;background:#0f1419;color:#e7ecf1}
a,label{color:#9ecbff} input,select,button,textarea{padding:.4rem .6rem;margin:.25rem .4rem .25rem 0;
  background:#1a2332;color:#e7ecf1;border:1px solid #333;border-radius:4px}
button{cursor:pointer;background:#2a4a6a} button:hover{background:#35608a}
.wrap{overflow:auto;max-height:70vh;border:1px solid #333;margin-bottom:1rem}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #333;padding:4px 8px;vertical-align:top;white-space:nowrap;
  max-width:420px;overflow:hidden;text-overflow:ellipsis}
th{background:#1a2332;position:sticky;top:0}
.err{color:#ff8a8a} .ok{color:#8dffb5} .meta{color:#8b9bb4;font-size:13px}
code,pre{background:#1a2332;padding:2px 6px;border-radius:4px}
pre{padding:10px;overflow:auto;white-space:pre-wrap;max-height:240px}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:1.2rem}
.nav{margin-bottom:1rem;padding:.6rem 0;border-bottom:1px solid #333}
.nav a{margin-right:1rem;text-decoration:none;font-weight:600}
.nav a.active{color:#fff;border-bottom:2px solid #6db3ff;padding-bottom:2px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#333}
.badge.pass{background:#1d4a32;color:#8dffb5}
.badge.fail{background:#5a1d1d;color:#ff8a8a}
.badge.pending{background:#4a3d1d;color:#ffd88a}
.badge.running{background:#1d3a5a;color:#9ecbff}
.badge.cancelled{background:#333;color:#aaa}
.tree-root td:first-child{font-weight:600}
.tree-child td:first-child{padding-left:1.8rem}
.tree-child2 td:first-child{padding-left:3rem}
.sel-row{background:#1a2a40}
.panel{border:1px solid #333;padding:1rem;margin:1rem 0;border-radius:6px;background:#121820}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.form-row{margin:.4rem 0}
"""


def nav_html(active: str) -> str:
    items = [
        ("/", "DB Browser", "browser"),
        ("/tasks", "Task Center", "tasks"),
        ("/health", "Code Health", "health"),
        ("/managed", "Managed Coding", "managed"),
        ("/pair", "Pair QC", "pair"),
        ("/hko", "HKO Proof", "hko"),
    ]
    links = []
    for href, label, key in items:
        cls = "active" if key == active else ""
        links.append(f'<a class="{cls}" href="{href}">{html.escape(label)}</a>')
    return f'<nav class="nav">{"".join(links)}</nav>'


def managed_page_html(
    report: dict | None,
    systems: list | None = None,
    *,
    err: str | None = None,
    flash: str | None = None,
    system_key: str = "membership",
    schema_ok: bool | None = None,
    builder: dict | None = None,
    selected_register_id: str | None = None,
    selected_slice: str | None = None,
) -> str:
    """MCS5/8 — Function Builder + worker-clean Managed Coding (pipeline D)."""
    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    flash_html = f'<p class="ok">{html.escape(flash)}</p>' if flash else ""
    schema_s = "ok" if schema_ok is True else ("missing" if schema_ok is False else "n/a")
    if report is None and not err:
        err_html = '<p class="err">managed_coding helpers unavailable</p>'

    sys_opts = []
    for s in systems or []:
        sk = str(s.get("system_key") or "")
        if not sk:
            continue
        sel = " selected" if sk == system_key else ""
        sys_opts.append(
            f'<option value="{html.escape(sk)}"{sel}>{html.escape(sk)}</option>'
        )
    if not sys_opts:
        sys_opts.append(
            f'<option value="{html.escape(system_key)}" selected>'
            f"{html.escape(system_key)}</option>"
        )
    builder = builder or {}

    def _rows(items: list, cols: tuple[str, ...]) -> str:
        if not items:
            return f'<tr><td colspan="{len(cols)}">(none)</td></tr>'
        lines = []
        for it in items:
            tds = []
            for c in cols:
                val = it.get(c)
                if c in ("task_id", "root_task_id", "slice_task_id") and val is not None:
                    try:
                        tds.append(
                            f'<td><a href="/tasks?task_id={int(val)}">#{int(val)}</a></td>'
                        )
                    except (TypeError, ValueError):
                        tds.append(f"<td><code>{html.escape(str(val))}</code></td>")
                elif c == "worker_bucket":
                    tds.append(f"<td>{status_badge(str(val or ''))}</td>")
                elif c == "missing_dims" and isinstance(val, list):
                    tds.append(
                        f"<td><code>{html.escape(', '.join(str(x) for x in val[:6]))}</code></td>"
                    )
                else:
                    tds.append(
                        f"<td><code>{html.escape(str('' if val is None else val))}</code></td>"
                    )
            lines.append("<tr>" + "".join(tds) + "</tr>")
        return "".join(lines)

    work = (report or {}).get("work_queue") or []
    skip = (report or {}).get("skip_rubbish") or []
    regs = (report or {}).get("registers_active") or (report or {}).get("registers") or []
    regs_rubbish_n = int((report or {}).get("registers_rubbish_n") or 0)
    rules = (report or {}).get("worker_rules") or []
    rules_li = "".join(f"<li>{html.escape(str(r))}</li>" for r in rules)
    naming = (report or {}).get("naming_law") or {}
    naming_html = (
        "<ul class='meta'>"
        f"<li><b>function</b> = <code>{html.escape(str(naming.get('function') or 'member_<slice>'))}</code></li>"
        f"<li><b>value</b> = {html.escape(str(naming.get('value') or 'runtime data'))}</li>"
        f"<li><b>_N suffix</b> = {html.escape(str(naming.get('suffix__N') or 're-seed noise'))}</li>"
        "</ul>"
    )
    loc = (report or {}).get("location") or {}
    ch = loc.get("channel") or {}
    mod = loc.get("module") or {}
    ver = loc.get("version") or {}
    task = loc.get("task") or {}
    plan = loc.get("plan") or {}
    regm = loc.get("register") or {}
    layer = (report or {}).get("layer") or builder.get("layer") or {}
    handoff = (report or {}).get("handoff") or {}
    this_layer = layer.get("this_layer") or {}
    not_layer = layer.get("not_this_layer") or {}
    owns_li = "".join(f"<li>{html.escape(str(x))}</li>" for x in (this_layer.get("owns") or []))
    not_owns_li = "".join(f"<li>{html.escape(str(x))}</li>" for x in (not_layer.get("owns") or []))
    handoff_keys_li = "".join(
        f"<li><code>{html.escape(str(x))}</code></li>"
        for x in (handoff.get("keys") or layer.get("handoff_keys") or [])
    )
    opt_dims_li = "".join(
        f"<li><code>{html.escape(str(x))}</code></li>"
        for x in (handoff.get("optional_dims") or layer.get("orchestrator_optional_dims") or [])
    )
    root_tid = loc.get("root_task_id")
    root_link = (
        f'<a href="/tasks?task_id={int(root_tid)}">#{int(root_tid)}</a>'
        if root_tid is not None
        else "—"
    )
    field_table = _rows(
        loc.get("fields") or [],
        ("slice_key", "data_table", "data_field", "function_name", "register_id", "tacid", "status"),
    )
    handoff_table = _rows(
        handoff.get("rows") or [],
        (
            "register_id",
            "slice_key",
            "plan_table",
            "data_field",
            "function_name",
            "slice_task_id",
            "tacid",
            "channel_code",
            "exec_driver",
            "orchestrator_status",
            "handoff_ready",
        ),
    )
    flow_ol = "".join(
        f"<li>{html.escape(str(x))}</li>"
        for x in (builder.get("flow") or layer.get("primary_flow") or [])
    )
    tdd_view = []
    for t in builder.get("tdd_rules") or []:
        rules_obj = t.get("rule_json")
        if isinstance(rules_obj, dict):
            rules_s = json.dumps(rules_obj.get("rules") or rules_obj, ensure_ascii=False)[:120]
        else:
            rules_s = str(rules_obj or "")[:120]
        deps = t.get("depends_on_json")
        deps_s = ",".join(str(x) for x in deps) if isinstance(deps, list) else str(deps or "")
        tdd_view.append(
            {
                "id": t.get("id"),
                "system_key": t.get("system_key"),
                "slice_key": t.get("slice_key"),
                "tdd_type_code": t.get("tdd_type_code"),
                "depends_on": deps_s,
                "register_id": t.get("register_id"),
                "task_id": t.get("task_id"),
                "rules": rules_s,
                "status": t.get("status"),
            }
        )
    req_table = _rows(
        builder.get("requests") or [],
        (
            "id",
            "request_key",
            "human_text",
            "channel_code",
            "module_code",
            "system_key",
            "desired_table",
            "status",
            "root_task_id",
            "created_at",
        ),
    )
    tdd_table = _rows(
        tdd_view,
        (
            "id",
            "system_key",
            "slice_key",
            "tdd_type_code",
            "depends_on",
            "register_id",
            "task_id",
            "rules",
            "status",
        ),
    )

    parts: list[str] = []
    parts.append(nav_html("managed"))
    parts.append("<h1>Managed Coding · Function Builder</h1>")
    parts.append(
        f'<p class="meta">Pipeline <code>D_managed_coding</code> · gate=<code>never</code> · '
        f"schema={html.escape(schema_s)} · law: <b>register_id + TDD</b> · "
        f'<a href="/api/managed?system={html.escape(system_key)}">/api/managed</a> · '
        f'<a href="/api/managed/builder?system={html.escape(system_key)}">/api/managed/builder</a></p>'
    )
    parts.append(err_html + flash_html)
    parts.append(
        '<p class="meta"><b>Flow:</b> human request → research (reuse/design-table) → '
        "multi-dim SSOT → build with <code>register_id</code> + per-field TDD</p>"
    )
    parts.append(
        '<form method="get" action="/managed" style="margin:.5rem 0">'
        f'<label>system <select name="system">{"".join(sys_opts)}</select></label> '
        '<button type="submit">Load worker report</button></form>'
    )
    parts.append(
        '<p><button type="button" onclick="mcsSeed()">Seed membership Task 1</button> '
        '<button type="button" onclick="mcsCleanup()">Mark demo noise rubbish</button> '
        '<button type="button" onclick="location.reload()">Refresh</button></p>'
    )

    # ---- Function Builder IA (L1.2): Task header + 8 actions + field contracts ----
    # Function ID is DB-driven: code_register.register_id (real table, not hardcode)
    fields_loc = list(loc.get("fields") or [])
    regs_active = list(
        (report or {}).get("registers_active")
        or (report or {}).get("registers")
        or []
    )
    # index registers by register_id / slice_key
    reg_by_id: dict[str, dict] = {}
    reg_by_slice: dict[str, dict] = {}
    for r in regs_active:
        rid0 = str(r.get("register_id") or "").strip()
        sk0 = str(r.get("slice_key") or "").strip()
        if rid0:
            reg_by_id[rid0] = r
        if sk0 and sk0 not in reg_by_slice:
            reg_by_slice[sk0] = r
    field_by_slice = {
        str(f.get("slice_key") or ""): f for f in fields_loc if f.get("slice_key")
    }

    sel_rid = (selected_register_id or "").strip() or None
    sel_slice = (selected_slice or "").strip() or None
    # resolve target register row from DB list
    target_reg = None
    if sel_rid and sel_rid in reg_by_id:
        target_reg = reg_by_id[sel_rid]
    elif sel_slice and sel_slice in reg_by_slice:
        target_reg = reg_by_slice[sel_slice]
    else:
        for pref in ("phone", "region", "name"):
            if pref in reg_by_slice:
                target_reg = reg_by_slice[pref]
                break
        if target_reg is None and regs_active:
            target_reg = regs_active[0]
    target_reg = target_reg or {}
    fn_rid = str(target_reg.get("register_id") or "").strip()
    fn_name = str(target_reg.get("function_name") or "").strip()
    fn_module = str(target_reg.get("module_name") or mod.get("code") or system_key or "").strip()
    fn_slice = str(target_reg.get("slice_key") or "").strip()
    fn_tacid = str(target_reg.get("tacid") or "").strip()
    fn_status = str(target_reg.get("status") or "").strip()
    fn_db_id = target_reg.get("id")
    fn_task_id = target_reg.get("slice_task_id") or target_reg.get("task_id")
    # merge location field map for related table/field
    target_field = field_by_slice.get(fn_slice) or {}
    tacid = fn_tacid or str(
        target_field.get("tacid")
        or task.get("task_label")
        or loc.get("root_task_label")
        or "x.x"
    )
    data_table = str(
        target_field.get("data_table") or plan.get("data_table") or "member"
    )
    data_field = str(
        target_field.get("data_field") or target_field.get("slice_key") or fn_slice or ""
    )
    related_tables = sorted(
        {
            str(f.get("data_table") or plan.get("data_table") or "member")
            for f in fields_loc
        }
        or {data_table}
    )
    related_fields = [
        f"{f.get('data_table') or data_table} | {f.get('data_field') or f.get('slice_key')}"
        for f in fields_loc
    ]
    # Function ID picker = code_register.register_id ONLY (never tacid / task_label)
    # Task ID (1.1 / 1.2) is a separate field — do not prefix options with it.
    fn_opts = []
    for r in sorted(
        regs_active,
        key=lambda x: (
            str(x.get("function_name") or ""),
            str(x.get("register_id") or ""),
            int(x.get("id") or 0),
        ),
    ):
        rid_o = str(r.get("register_id") or "")
        if not rid_o:
            continue
        fname = str(r.get("function_name") or "").strip()
        # label: Function ID first; name is secondary hint only
        label = f"{rid_o}" + (f"  ({fname})" if fname else "")
        sel = " selected" if rid_o == fn_rid else ""
        fn_opts.append(
            f'<option value="{html.escape(rid_o)}"{sel}>{html.escape(label)}</option>'
        )
    if not fn_opts:
        fn_opts.append('<option value="">(no active code_register rows)</option>')

    # TDD templates for contract cards
    try:
        from managed_coding import FIELD_TDD_TEMPLATES as _TDD_TMPL
    except Exception:
        _TDD_TMPL = {}
    tdd_by_slice = {}
    for t in builder.get("tdd_rules") or []:
        tdd_by_slice[str(t.get("slice_key") or "")] = t

    def _contract_card(slice_key: str) -> str:
        tmpl = dict(_TDD_TMPL.get(slice_key) or {})
        row = tdd_by_slice.get(slice_key) or {}
        rule = row.get("rule_json") if isinstance(row.get("rule_json"), dict) else tmpl
        if isinstance(rule, str):
            try:
                rule = json.loads(rule)
            except Exception:
                rule = tmpl
        # prefer live register_id from code_register for this slice
        live_reg = reg_by_slice.get(slice_key) or {}
        note = html.escape(str((rule or {}).get("note") or tmpl.get("note") or ""))
        example = html.escape(str((rule or {}).get("example") or tmpl.get("example") or ""))
        vtype = html.escape(str((rule or {}).get("value_type") or tmpl.get("value_type") or "string"))
        storage = html.escape(str((rule or {}).get("storage_type") or tmpl.get("storage_type") or vtype))
        deps = (rule or {}).get("depends_on") or tmpl.get("depends_on") or []
        deps_s = html.escape(", ".join(str(x) for x in deps) if deps else "none")
        fail = html.escape(str((rule or {}).get("fail_class") or tmpl.get("fail_class") or "business_defect"))
        op = html.escape(str((rule or {}).get("op") or tmpl.get("op") or ""))
        pat = html.escape(str((rule or {}).get("pattern") or ""))
        if not pat and isinstance((rule or {}).get("by_region"), dict):
            pats = []
            for k, v in list((rule or {}).get("by_region").items())[:4]:
                if isinstance(v, dict) and v.get("pattern"):
                    pats.append(f"{k}:{v.get('pattern')}")
            pat = html.escape("; ".join(pats))
        rid = html.escape(
            str(live_reg.get("register_id") or row.get("register_id") or "")
        )
        tid = live_reg.get("slice_task_id") or live_reg.get("task_id") or row.get("task_id")
        tid_s = f'<a href="/tasks?task_id={int(tid)}">#{int(tid)}</a>' if tid is not None else "—"
        if slice_key == "region":
            law = (
                "type: <b>string</b> (NOT INT) · storage <code>+CC</code> · "
                "example <code>+86</code> · primary dial policy · ISO2 display-only"
            )
            conf = "source: human / MCP / ISO2→CC map · confirm N/A (not SMS target)"
            tdd_law = "TDD: <code>^\\+\\d{1,4}$</code> · not letters · not emoji · not full phone"
        elif slice_key == "phone":
            law = (
                "type: <b>digit_string</b> (NOT INT) · local only · "
                "example <code>13800138000</code> · no <code>+</code> · length by region"
            )
            conf = "confirmation: SMS / WhatsApp / … (runtime) · depends_on <code>region</code>"
            tdd_law = (
                "TDD: digits only · not text/symbol/emoji · "
                "+86 → 11 local · e164=compose only"
            )
        elif slice_key == "country":
            law = (
                "type: <b>text</b> (NOT INT) · lookup from region → country_name · "
                "example <code>China</code>"
            )
            conf = "source: match region to country table · display only"
            tdd_law = "TDD: not INT · not symbol · not emoji · text name"
        else:
            law = f"type: <b>{vtype}</b> · storage <code>{storage}</code>"
            conf = f"depends_on: <code>{deps_s}</code>"
            tdd_law = f"op=<code>{op}</code> pattern=<code>{pat}</code>"
        return (
            f'<div class="panel contract-card" data-slice="{html.escape(slice_key)}">'
            f"<h3>value · <code>{html.escape(slice_key)}</code></h3>"
            f'<p class="meta">{law}</p>'
            f'<p class="meta">{conf}</p>'
            f'<p class="meta">{tdd_law}</p>'
            f'<p class="meta">example: <code>{example or "—"}</code> · '
            f"fail_class=<code>{fail}</code> · depends=<code>{deps_s}</code></p>"
            f'<p class="meta">note: {note or "—"}</p>'
            f'<p class="meta">Function ID=<code>{rid or "—"}</code> '
            f"(<code>code_register.register_id</code>) · task={tid_s} · "
            f"op=<code>{op}</code></p>"
            f"{(f'<p class=\"meta\">pattern: <code>{pat}</code></p>' if pat else '')}"
            f"</div>"
        )

    action_defs = [
        ("function.create", "create function", "Declare/register function body binding"),
        ("function.update", "update function", "Revise existing function binding"),
        ("api.create", "create API", "Declare API surface for this function"),
        ("api.update", "update API", "Revise API surface"),
        ("table.create", "create Table", "Declare domain table (migrate task)"),
        ("table.update", "update Table", "Alter domain table schema (migrate task)"),
        ("field.create", "create Field", "Declare column / SSOT field"),
        ("field.update", "update Field", "Revise field contract / TDD"),
    ]
    action_cards = []
    for code, label, tip in action_defs:
        action_cards.append(
            '<div class="action-card">'
            f'<div class="action-title"><code>{html.escape(code)}</code><br/>'
            f"<b>{html.escape(label)}</b></div>"
            f'<p class="meta">{html.escape(tip)}</p>'
            f'<p class="meta">parent Task ID <code>{html.escape(tacid)}</code> · '
            f"Function ID <code>{html.escape(fn_rid or '—')}</code> · "
            "one verb = one child sub-task</p>"
            f'<button type="button" disabled title="declare-only scaffold">'
            f"{html.escape(label)} (declare)</button>"
            "</div>"
        )

    parts.append('<div class="panel builder-banner">')
    parts.append(
        "<h2>Function Builder · Task header + action sub-tasks + field contracts</h2>"
    )
    parts.append(
        '<p class="meta">Law L1.2: <code>region=+CC</code> · '
        "<code>phone=local digits</code> · <code>e164=compose</code> · "
        "one action = one child task · MCS declares, Pair QC detects</p>"
    )
    parts.append('<div class="panel target-header">')
    parts.append("<h3>Target header</h3>")
    parts.append(
        '<p class="meta"><b>Function ID</b> = <code>code_register.register_id</code> only · '
        "<b>Task ID</b> = <code>dev_task.task_label</code> / tacid (e.g. <code>1.2</code>) — "
        "<b>not the same thing</b></p>"
    )
    parts.append(
        '<form method="get" action="/managed" class="fn-pick" style="margin:.4rem 0 .6rem">'
        f'<input type="hidden" name="system" value="{html.escape(system_key)}"/>'
        '<label><b>Function ID</b> '
        f'<select name="register_id" onchange="this.form.submit()">{"".join(fn_opts)}</select>'
        "</label> "
        '<span class="meta">DB: <code>code_register.register_id</code> · value never starts with task label</span>'
        "</form>"
    )
    fn_task_link = (
        f'<a href="/tasks?task_id={int(fn_task_id)}">#{int(fn_task_id)}</a>'
        if fn_task_id is not None
        else "—"
    )
    parts.append('<div class="grid2"><div>')
    parts.append(
        f'<p><b>Task ID</b>: <code>{html.escape(tacid)}</code> '
        f'<span class="meta">(task_label / tacid — NOT Function ID)</span><br/>'
        f'slice_task {fn_task_link} · root {root_link}</p>'
    )
    parts.append(
        f'<p><b>module</b>: <code>{html.escape(fn_module or str(mod.get("code") or system_key or "membership"))}</code> '
        f'(<code>code_register.module_name</code>)</p>'
    )
    parts.append(
        f'<p><b>Function ID</b>: <code>{html.escape(fn_rid or "—")}</code><br/>'
        f'<span class="meta">= <code>code_register.register_id</code>'
        f'{(" · pk id=" + str(fn_db_id)) if fn_db_id is not None else ""}'
        f'{(" · status=" + html.escape(fn_status)) if fn_status else ""}</span></p>'
    )
    parts.append(
        f'<p><b>Function name</b>: <code>{html.escape(fn_name or "—")}</code> '
        f'(<code>code_register.function_name</code>)</p>'
    )
    parts.append(
        f'<p><b>slice_key</b>: <code>{html.escape(fn_slice or "—")}</code></p>'
    )
    parts.append("</div><div>")
    parts.append(
        f'<p><b>Related Table</b>: <code>{html.escape(", ".join(related_tables))}</code></p>'
    )
    parts.append(
        f'<p><b>Related Field</b>: <code>{html.escape(" · ".join(related_fields) if related_fields else (data_table + " | " + data_field))}</code></p>'
    )
    parts.append(
        f'<p><b>channel</b>: <code>{html.escape(str(ch.get("code") or "local_pc"))}</code> · '
        f'version <code>{html.escape(str(ver.get("version_label") or ""))}</code></p>'
    )
    parts.append(
        f'<p><b>equation</b>: <code>{html.escape(str(plan.get("equation") or ""))}</code></p>'
    )
    parts.append(
        '<p class="meta">law: no <code>register_id</code> → not managed code · '
        "Task ID <code>1.x</code> is coordinate only</p>"
    )
    parts.append("</div></div></div>")

    parts.append("<h3>Actions · each verb is a separate child sub-task</h3>")
    parts.append(
        '<p class="meta">create|update × function|API|table|field → 8 affordances · '
        "buttons are declare-only scaffolds (no auto DDL / no auto-fix)</p>"
    )
    parts.append('<div class="action-grid">' + "".join(action_cards) + "</div>")

    parts.append("<h3>Field contracts · region / phone / country</h3>")
    parts.append(
        '<p class="meta"><b>+86 is region NOT phone</b> · phone example '
        "<code>13800138000</code> · country is text lookup · never store full E.164 as phone</p>"
    )
    parts.append('<div class="grid3">')
    for sk in ("region", "phone", "country"):
        parts.append(_contract_card(sk))
    parts.append("</div>")

    # keep legacy request form collapsed under advanced
    parts.append('<details class="panel" style="margin-top:.8rem"><summary><b>Advanced · Research + Build system</b></summary>')
    parts.append(f'<ol class="flow">{flow_ol or "<li>human request → research → build</li>"}</ol>')
    parts.append('<div class="grid2"><div class="panel"><h3>1) Human request</h3>')
    parts.append(
        '<div class="form-row"><label>request text<br/>'
        '<textarea id="fb_text" rows="3" style="width:100%">I need a membership system</textarea>'
        "</label></div>"
    )
    parts.append(
        '<div class="form-row"><label>channel <input id="fb_channel" value="local_pc"/></label> '
        '<label>module <input id="fb_module" value="membership"/></label></div>'
    )
    parts.append(
        '<div class="form-row"><label>system_key <input id="fb_system" value="membership"/></label> '
        '<label>table <input id="fb_table" value="member" placeholder="empty = design table"/></label></div>'
    )
    parts.append(
        '<div class="form-row"><label>fields (comma) '
        '<input id="fb_fields" style="width:100%" value="region,phone,contact_method,name,gender"/>'
        "</label></div>"
    )
    parts.append(
        '<div class="form-row"><label>output <input id="fb_output" value="member_profile"/></label></div>'
    )
    parts.append(
        '<p><button type="button" onclick="fbBuild()">Research + Build system</button> '
        '<span class="meta">slices · register_id · field TDD · multi-dim SSOT</span></p>'
    )
    parts.append(
        '<p class="meta">path: with table → 1.1+region… · without → design 1.1+member_id…</p></div>'
    )
    parts.append(
        '<div class="panel"><h3>TDD law (locked)</h3><ul class="meta">'
        "<li><code>region</code> = <b>+CC</b> primary (e.g. <code>+86</code>) · string · not INT</li>"
        "<li><code>phone</code> = <b>local digits</b> (e.g. <code>13800138000</code>) · depends on region</li>"
        "<li><code>e164</code> = compose only · never master storage for phone</li>"
        "<li>country = text lookup from region · not INT</li>"
        "<li>rules in <code>field_tdd_rule.rule_json</code></li>"
        "</ul><h3>Traceability</h3>"
        '<p class="meta">request_id → research → root_task → slice_task → register_id → tdd_rule_id</p>'
        "</div></div>"
    )
    parts.append(
        "<h3>Recent requests</h3><div class=\"wrap\"><table><thead><tr>"
        "<th>id</th><th>key</th><th>text</th><th>channel</th><th>module</th><th>system</th>"
        "<th>table</th><th>status</th><th>root</th><th>created</th></tr></thead>"
        f"<tbody>{req_table}</tbody></table></div>"
    )
    parts.append(
        "<h3>Field TDD rules</h3><div class=\"wrap\"><table><thead><tr>"
        "<th>id</th><th>system</th><th>field</th><th>tdd</th><th>depends</th>"
        "<th>register_id</th><th>task</th><th>rules</th><th>status</th></tr></thead>"
        f"<tbody>{tdd_table}</tbody></table></div></details></div>"
    )

    parts.append('<div class="panel layer-banner">')
    parts.append(
        '<h2>Layer map · Ontology & Registry <span class="pill">MCS · Pipeline D · never gate</span></h2>'
    )
    parts.append(
        f"<p><b>{html.escape(str(layer.get('positioning') or 'Managed Coding = Ontology & Registry + Function Builder.'))}</b></p>"
    )
    parts.append(
        f'<p class="meta">flow: <code>{html.escape(str(handoff.get("flow") or layer.get("flow") or ""))}</code></p>'
    )
    parts.append(
        f'<div class="grid2"><div class="panel in-scope"><h3>This page owns</h3>'
        f"<ul>{owns_li or '<li>(contracts)</li>'}</ul></div>"
        f'<div class="panel out-scope"><h3>Not this page (runtime orchestrator)</h3>'
        f"<ul>{not_owns_li or '<li>(driver / evidence / pair QC)</li>'}</ul>"
        f'<p class="meta">{html.escape(str(not_layer.get("note") or ""))}</p></div></div>'
    )
    parts.append(
        f'<div class="grid2"><div><h3>Handoff keys</h3>'
        f'<ul class="meta">{handoff_keys_li or "<li><code>register_id</code></li>"}</ul></div>'
        f"<div><h3>Optional orchestrator dims</h3>"
        f'<ul class="meta">{opt_dims_li or "<li><code>exec.driver</code></li>"}</ul></div></div></div>'
    )

    parts.append('<div class="panel"><h2>Where in DB (table · field)</h2>')
    parts.append(
        f'<p class="meta">module=<code>{html.escape(str(mod.get("code") or ""))}</code> on channel='
        f'<code>{html.escape(str(ch.get("code") or "local_pc"))}</code> · channel = env ontology</p>'
    )
    parts.append(
        '<div class="wrap"><table><thead><tr><th>concept</th><th>table</th><th>field</th>'
        "<th>value</th><th>notes</th></tr></thead><tbody>"
    )
    parts.append(
        f"<tr><td><b>channel</b></td><td><code>channel</code></td><td><code>code</code></td>"
        f"<td><code>{html.escape(str(ch.get('code') or ''))}</code></td><td>FK dev_task.channel_id</td></tr>"
    )
    parts.append(
        f"<tr><td><b>module</b></td><td><code>module</code></td><td><code>code</code></td>"
        f"<td><code>{html.escape(str(mod.get('code') or ''))}</code></td><td>FK dev_task.module_id</td></tr>"
    )
    parts.append(
        f"<tr><td><b>version</b></td><td><code>version_center</code></td><td><code>version_label</code></td>"
        f"<td><code>{html.escape(str(ver.get('version_label') or ''))}</code></td><td>FK dev_task.version_id</td></tr>"
    )
    parts.append(
        f"<tr><td><b>task</b></td><td><code>dev_task</code></td><td><code>task_label</code></td>"
        f"<td>root {root_link} <code>{html.escape(str(task.get('task_label') or loc.get('root_task_label') or ''))}</code></td>"
        f"<td>parent_task_id tree</td></tr>"
    )
    parts.append(
        f"<tr><td><b>system key</b></td><td><code>task_ssot</code></td><td><code>system.key</code></td>"
        f"<td><code>{html.escape(str(loc.get('system_key') or system_key))}</code></td><td>multi-dim SSOT</td></tr>"
    )
    parts.append(
        f"<tr><td><b>plan table</b></td><td><code>task_ssot</code></td><td><code>plan.table</code></td>"
        f"<td><code>{html.escape(str(plan.get('data_table') or ''))}</code></td><td>domain table</td></tr>"
    )
    parts.append(
        f"<tr><td><b>equation</b></td><td><code>task_ssot</code></td><td><code>goal.equation</code></td>"
        f"<td><code>{html.escape(str(plan.get('equation') or ''))}</code></td><td>value+value=output</td></tr>"
    )
    parts.append(
        f"<tr><td><b>register</b></td><td><code>code_register</code></td><td><code>register_id</code></td>"
        f"<td>active registers</td><td>{html.escape(str(regm.get('note') or 'join via task'))}</td></tr>"
    )
    parts.append(
        "<tr><td><b>request</b></td><td><code>fn_request</code></td><td><code>request_key</code></td>"
        "<td>builder requests</td><td>human→research→build</td></tr>"
    )
    parts.append(
        "<tr><td><b>TDD</b></td><td><code>field_tdd_rule</code></td><td><code>rule_json</code></td>"
        "<td>per field</td><td>bound to register_id</td></tr></tbody></table></div>"
    )
    parts.append(
        f'<p class="meta">join: <code>{html.escape(str(loc.get("join_path") or ""))}</code></p>'
        f"<h3>Slice → data field map</h3><div class=\"wrap\"><table><thead><tr>"
        f"<th>slice</th><th>data table</th><th>data field</th><th>function</th>"
        f"<th>register_id</th><th>tacid</th><th>status</th>"
        f"</tr></thead><tbody>{field_table}</tbody></table></div></div>"
    )

    parts.append(
        f'<div class="panel"><h2>Orchestrator handoff (downstream)</h2>'
        f'<p class="meta">ready=<b>{int(handoff.get("ready_n") or 0)}</b> · '
        f'bound=<b>{int(handoff.get("bound_n") or 0)}</b></p>'
        f'<div class="wrap"><table><thead><tr>'
        f"<th>register_id</th><th>slice</th><th>plan.table</th><th>field</th><th>function</th>"
        f"<th>slice_task</th><th>tacid</th><th>channel</th><th>exec.driver</th><th>status</th><th>ready</th>"
        f"</tr></thead><tbody>{handoff_table}</tbody></table></div></div>"
    )

    parts.append('<div class="grid2"><div class="panel">')
    parts.append(f'<h2>Summary · <code>{html.escape(system_key)}</code></h2><ul>')
    parts.append(f'<li>channel: <code>{html.escape(str(ch.get("code") or ""))}</code></li>')
    parts.append(f'<li>module: <code>{html.escape(str(mod.get("code") or ""))}</code></li>')
    parts.append(f'<li>version: <code>{html.escape(str(ver.get("version_label") or ""))}</code></li>')
    parts.append(f"<li>root task: {root_link}</li>")
    parts.append(f'<li>equation: <code>{html.escape(str(plan.get("equation") or ""))}</code></li>')
    parts.append(f'<li>active slices: <b>{int((report or {}).get("active_n") or 0)}</b></li>')
    parts.append(f'<li>incomplete: <b>{int((report or {}).get("incomplete_n") or 0)}</b></li>')
    parts.append(f'<li>needs_register: <b>{int((report or {}).get("draft_n") or 0)}</b></li>')
    parts.append(f'<li>active registers: <b>{int((report or {}).get("register_n") or 0)}</b></li>')
    parts.append(f"<li>rubbish registers: <b>{regs_rubbish_n}</b></li>")
    parts.append(f'<li>handoff ready: <b>{int(handoff.get("ready_n") or 0)}</b></li></ul></div>')
    parts.append(
        f'<div class="panel"><h2>Naming law</h2>{naming_html}'
        f'<h2>Worker rules</h2><ul class="meta">{rules_li or "<li>(none)</li>"}</ul></div></div>'
    )

    parts.append(
        '<div class="panel"><h2>Work queue</h2><div class="wrap"><table><thead><tr>'
        "<th>task</th><th>label</th><th>title</th><th>bucket</th><th>register_id</th><th>missing</th><th>action</th>"
        f'</tr></thead><tbody>{_rows(work, ("task_id", "task_label", "title", "worker_bucket", "register_id", "missing_dims", "worker_action"))}</tbody></table></div></div>'
    )
    parts.append(
        '<div class="panel"><h2>Skip rubbish</h2><div class="wrap"><table><thead><tr>'
        "<th>task</th><th>label</th><th>title</th><th>bucket</th><th>register_id</th>"
        f'</tr></thead><tbody>{_rows(skip, ("task_id", "task_label", "title", "worker_bucket", "register_id"))}</tbody></table></div></div>'
    )
    parts.append(
        '<div class="panel"><h2>Registers — active only</h2>'
        '<p class="meta">function <code>member_region</code> + value <code>TW</code> · not <code>member_region_5</code></p>'
        '<div class="wrap"><table><thead><tr>'
        "<th>register_id</th><th>module</th><th>function</th><th>tacid</th><th>status</th><th>slice</th>"
        f'</tr></thead><tbody>{_rows(regs, ("register_id", "module_name", "function_name", "tacid", "status", "slice_key"))}</tbody></table></div></div>'
    )

    sk_js = html.escape(system_key)
    parts.append(
        """
<script>
async function mcsSeed(){
  const r=await fetch('/api/managed/seed',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const j=await r.json();
  if(!r.ok){ alert(j.error||JSON.stringify(j)); return; }
  location.href='/managed?system=membership&flash='+encodeURIComponent(
    'seed root='+(j.root_task_id||'')+' slices='+((j.slices||[]).length)+' regs='+((j.registers||[]).length)
  );
}
async function mcsCleanup(){
  const r=await fetch('/api/managed/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const j=await r.json();
  if(!r.ok){ alert(j.error||JSON.stringify(j)); return; }
  location.href='/managed?system="""
        + sk_js
        + """&flash='+encodeURIComponent(
    'marked tasks='+((j.marked_tasks||[]).length)+' regs='+((j.marked_registers||[]).length)
  );
}
async function fbBuild(){
  const body={
    text: document.getElementById('fb_text').value,
    module: document.getElementById('fb_module').value,
    channel: document.getElementById('fb_channel').value,
    system: document.getElementById('fb_system').value,
    table: document.getElementById('fb_table').value,
    output: document.getElementById('fb_output').value,
    fields: document.getElementById('fb_fields').value
  };
  const r=await fetch('/api/managed/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(!r.ok){ alert(j.error||JSON.stringify(j)); return; }
  const b=j.build||{};
  const sk=b.system_key||body.system||'membership';
  location.href='/managed?system='+encodeURIComponent(sk)+'&flash='+encodeURIComponent(
    'built req='+((j.request&&j.request.request_key)||'')+
    ' root='+(b.root_task_id||'')+
    ' slices='+((b.slices||[]).length)+
    ' regs='+((b.registers||[]).length)+
    ' tdd='+((b.tdd_rules||[]).length)+
    ' path='+(b.path_code||'')
  );
}
</script>
"""
    )

    body = "\n".join(parts)
    return (
        "<!doctype html>\n<html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"/>\n"
        "<title>Managed Coding · Function Builder</title>\n<style>"
        + SHARED_CSS
        + """
.ok{color:#8dffb5}
ul{line-height:1.6}
ol.flow{line-height:1.55;margin:.4rem 0 .8rem 1.2rem}
.layer-banner{border:1px solid #3d6a9e;background:#132033}
.builder-banner{border:1px solid #6a5a2e;background:#1c1a12}
.in-scope{border-color:#2f6f4e;background:#102418}
.out-scope{border-color:#6f3a2f;background:#241410}
.pill{display:inline-block;padding:.1rem .45rem;border-radius:999px;background:#24344a;font-size:.8rem;margin-left:.35rem}
input,textarea,select{background:#0d1117;color:#e6edf3;border:1px solid #333;padding:.25rem .4rem;border-radius:4px}
button{cursor:pointer;margin-right:.35rem}
.target-header{border:1px solid #3d6a9e;background:#101820;margin:.5rem 0}
.action-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:.6rem;margin:.5rem 0 1rem}
.action-card{border:1px solid #444;background:#141414;padding:.55rem .65rem;border-radius:6px}
.action-title{margin-bottom:.25rem;line-height:1.35}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.6rem}
.contract-card{border:1px solid #5a4a2a;background:#1a160e}
.contract-card h3{margin-top:0}
button:disabled{opacity:.55;cursor:not-allowed}
</style></head><body>
"""
        + body
        + "\n</body></html>"
    )



def _health_fn_rows(items: list, *, extra_cols: tuple[str, ...] = ()) -> str:
    if not items:
        return '<tr><td colspan="10">(none)</td></tr>'
    lines = []
    for it in items:
        mod = html.escape(str(it.get("module") or it.get("module_name") or ""))
        fn = html.escape(str(it.get("function") or it.get("function_name") or ""))
        usage = html.escape(str(it.get("usage", it.get("total_invocations", ""))))
        pass_n = html.escape(str(it.get("pass_count", it.get("success_count", ""))))
        fail_n = html.escape(str(it.get("fail_count", "")))
        score = it.get("score")
        if score is None:
            score = it.get("functional_score")
        score_s = html.escape("" if score is None else str(score))
        status = html.escape(str(it.get("status") or ""))
        file_line = it.get("file_line")
        if not file_line and it.get("file_path"):
            file_line = (
                f"{it.get('file_path')}:{it.get('line_start')}-{it.get('line_end')}"
            )
        file_s = html.escape(str(file_line or ""))
        rid = html.escape(str(it.get("register_id") or ""))
        static = it.get("ref_static_tacids") or []
        hits = it.get("ref_hit_tacids") or []
        static_s = html.escape(", ".join(str(x) for x in static[:6]))
        hits_s = html.escape(", ".join(str(x) for x in hits[:6]))
        lines.append(
            "<tr>"
            f"<td><code>{mod}</code></td>"
            f"<td><code>{fn}</code></td>"
            f"<td><code>{file_s}</code></td>"
            f"<td>{usage}</td>"
            f"<td>{pass_n}</td>"
            f"<td>{fail_n}</td>"
            f"<td>{score_s}</td>"
            f"<td>{status_badge(status) if status else ''}</td>"
            f"<td><code>{rid}</code></td>"
            f"<td><code>{static_s}</code> / <code>{hits_s}</code></td>"
            "</tr>"
        )
    return "".join(lines)


def health_page_html(
    report: dict | None,
    branch: dict | None = None,
    *,
    err: str | None = None,
    flash: str | None = None,
    prefix: str = "ch.demo",
    schema_ok: bool | None = None,
) -> str:
    """CH6/CH7 — Code Health panel (pipeline C, never a gate)."""
    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    flash_html = f'<p class="ok">{html.escape(flash)}</p>' if flash else ""
    if report is None:
        body = f"""{nav_html("health")}
<h1>Code Health</h1>
{err_html}{flash_html}
<p class="meta">Pipeline C · gate=<code>never</code> · score is objective only · no source delete</p>
<p class="err">code_health helpers unavailable</p>
"""
        return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>Code Health</title><style>{SHARED_CSS}</style></head><body>
{body}</body></html>"""

    z_rows = _health_fn_rows(report.get("zombie_functions") or [])
    d_rows = _health_fn_rows(report.get("dead_candidate_functions") or [])
    l_rows = _health_fn_rows(report.get("low_score_functions") or [])
    schema_s = (
        "ok"
        if schema_ok is True
        else ("missing" if schema_ok is False else "n/a")
    )
    branch_block = ""
    if branch:
        b_lines = []
        for f in branch.get("functions") or []:
            b_lines.append(
                "<tr>"
                f'<td><code>{html.escape(str(f.get("module_name") or ""))}</code></td>'
                f'<td><code>{html.escape(str(f.get("function_name") or ""))}</code></td>'
                f'<td>{html.escape(str(f.get("hits") or 0))}</td>'
                f'<td>{html.escape(str(f.get("success") or 0))}/{html.escape(str(f.get("fail") or 0))}</td>'
                f'<td><code>{html.escape(", ".join(str(x) for x in (f.get("tacids") or [])[:8]))}</code></td>'
                "</tr>"
            )
        branch_block = f"""
<div class="panel">
<h2>Branch hits · prefix <code>{html.escape(prefix)}</code> · {int(branch.get("function_count") or 0)} fns</h2>
<p class="meta">{html.escape(str(branch.get("boundary_note") or ""))}</p>
<div class="wrap"><table>
<thead><tr><th>module</th><th>function</th><th>hits</th><th>ok/fail</th><th>tacids</th></tr></thead>
<tbody>{"".join(b_lines) or '<tr><td colspan="5">(none)</td></tr>'}</tbody>
</table></div>
</div>
"""
    report_time = html.escape(str(report.get("report_time") or ""))
    notes = html.escape(str(report.get("notes") or ""))
    body = f"""
{nav_html("health")}
<h1>Code Health</h1>
<p class="meta">Pipeline <code>C_code_health</code> · gate=<code>never</code> ·
schema={html.escape(schema_s)} · report_time={report_time}</p>
{err_html}{flash_html}
<p class="meta">WHY W4/W5/W7/W11/W12 · {notes}</p>
<p class="meta">score = usage>0 ? pass/usage*100 : null · file:line on register · cleanup tasks mark-only</p>
<p>
  <button type="button" onclick="healthHarvest()">Harvest task_ssot impl.*</button>
  <button type="button" onclick="healthCleanup()">Spawn dead cleanup tasks</button>
  <button type="button" onclick="location.reload()">Refresh report</button>
  <a class="meta" href="/api/health">/api/health</a>
  · <a class="meta" href="/api/health/branch?prefix={html.escape(prefix)}">/api/health/branch</a>
</p>
<form method="get" action="/health" style="margin:.5rem 0">
  <label>branch prefix <input name="prefix" value="{html.escape(prefix)}" size="24"/></label>
  <button type="submit">Branch report</button>
</form>
<div class="panel">
<h2>Bind source location (file + line)</h2>
<p class="meta">example: <code>test.py</code> line 1–3 · optional code span</p>
<div class="form-row">
  <label>register_id <input id="hb_reg" placeholder="reg_..." size="28"/></label>
  <label>or module <input id="hb_mod" placeholder="demo" size="12"/></label>
  <label>function <input id="hb_fn" placeholder="fn" size="16"/></label>
</div>
<div class="form-row">
  <label>file <input id="hb_file" value="test.py" size="24"/></label>
  <label>line_start <input id="hb_ls" type="number" value="1" min="1" style="width:5rem"/></label>
  <label>line_end <input id="hb_le" type="number" value="3" min="1" style="width:5rem"/></label>
</div>
<div class="form-row">
  <label>code span (optional)<br/>
  <textarea id="hb_code" rows="2" style="width:100%" placeholder="optional body snapshot"></textarea></label>
</div>
<p><button type="button" onclick="healthBind()">Bind file+line</button></p>
</div>
<div class="grid2">
  <div class="panel">
    <h2>Summary</h2>
    <ul>
      <li>active: <b>{int(report.get("active_count") or 0)}</b></li>
      <li>inactive: <b>{int(report.get("inactive_count") or 0)}</b></li>
      <li>zombie: <b>{len(report.get("zombie_functions") or [])}</b></li>
      <li>dead_candidate: <b>{len(report.get("dead_candidate_functions") or [])}</b></li>
      <li>low_score (<{html.escape(str(report.get("score_low_threshold") or 60))}):
          <b>{len(report.get("low_score_functions") or [])}</b></li>
      <li>scoring rows: <b>{int(report.get("scoring_row_count") or 0)}</b></li>
    </ul>
  </div>
  <div class="panel">
    <h2>Rules (read-only)</h2>
    <ul class="meta">
      <li>zombie = declared in task_ssot impl.* & usage=0</li>
      <li>dead_candidate = no static refs & usage=0</li>
      <li>usage / pass / fail · score = pass/usage*100</li>
      <li>file:line + code_span on <code>code_register</code></li>
      <li>watchdog/health → <code>code.cleanup</code> tasks (mark only)</li>
      <li>no auto-delete source · never a QC gate</li>
    </ul>
  </div>
</div>
<div class="panel">
<h2>Zombie functions</h2>
<div class="wrap"><table>
<thead><tr><th>module</th><th>function</th><th>file:line</th><th>usage</th><th>pass</th><th>fail</th><th>score</th><th>status</th><th>register_id</th><th>static/hit</th></tr></thead>
<tbody>{z_rows}</tbody></table></div>
</div>
<div class="panel">
<h2>Dead candidates</h2>
<div class="wrap"><table>
<thead><tr><th>module</th><th>function</th><th>file:line</th><th>usage</th><th>pass</th><th>fail</th><th>score</th><th>status</th><th>register_id</th><th>static/hit</th></tr></thead>
<tbody>{d_rows}</tbody></table></div>
</div>
<div class="panel">
<h2>Low score</h2>
<div class="wrap"><table>
<thead><tr><th>module</th><th>function</th><th>file:line</th><th>usage</th><th>pass</th><th>fail</th><th>score</th><th>status</th><th>register_id</th><th>static/hit</th></tr></thead>
<tbody>{l_rows}</tbody></table></div>
</div>
{branch_block}
<script>
async function healthHarvest(){{
  const r=await fetch('/api/health/harvest',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}});
  const j=await r.json();
  if(!r.ok){{ alert(j.error||JSON.stringify(j)); return; }}
  location.href='/health?flash='+encodeURIComponent('harvest zombies='+(j.zombie_count||0)+' touched='+(j.touched||0));
}}
async function healthCleanup(){{
  const r=await fetch('/api/health/cleanup',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}});
  const j=await r.json();
  if(!r.ok){{ alert(j.error||JSON.stringify(j)); return; }}
  location.href='/health?flash='+encodeURIComponent('cleanup created='+(j.created_n||0)+' skipped='+(j.skipped_n||0));
}}
async function healthBind(){{
  const body={{
    register_id: document.getElementById('hb_reg').value,
    module: document.getElementById('hb_mod').value,
    function: document.getElementById('hb_fn').value,
    file: document.getElementById('hb_file').value,
    line_start: Number(document.getElementById('hb_ls').value||1),
    line_end: Number(document.getElementById('hb_le').value||1),
    code: document.getElementById('hb_code').value
  }};
  const r=await fetch('/api/health/bind',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
  const j=await r.json();
  if(!r.ok){{ alert(j.error||JSON.stringify(j)); return; }}
  location.href='/health?flash='+encodeURIComponent('bound '+(j.file_path||'')+':'+(j.line_start||'')+'-'+(j.line_end||'')+' reg='+(j.register_id||''));
}}
</script>
"""
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>Code Health · Task Center</title>
<style>{SHARED_CSS}
.ok{{color:#8dffb5}}
ul{{line-height:1.6}}
</style></head><body>
{body}
</body></html>"""


def pair_page_html(
    dash: dict | None,
    *,
    err: str | None = None,
    flash: str | None = None,
    schema_ok: bool | None = None,
) -> str:
    """Pipeline E — Pair QC dual-path detect UI (never a gate)."""
    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    flash_html = f'<p class="ok">{html.escape(flash)}</p>' if flash else ""
    schema_s = "ok" if schema_ok is True else ("missing" if schema_ok is False else "n/a")
    dash = dash or {}
    counts = dash.get("counts") or {}
    runs = dash.get("runs") or []
    flow = dash.get("flow") or []
    flow_ol = "".join(f"<li>{html.escape(str(x))}</li>" for x in flow)
    rows = []
    for r in runs:
        rows.append(
            "<tr>"
            f'<td>{html.escape(str(r.get("id") or ""))}</td>'
            f'<td><code>{html.escape(str(r.get("field_name") or ""))}</code></td>'
            f'<td><code>{html.escape(str(r.get("region") or ""))}</code></td>'
            f'<td><code>{html.escape(str(r.get("mcp_norm") or ""))}</code></td>'
            f'<td><code>{html.escape(str(r.get("ui_norm") or ""))}</code></td>'
            f'<td>{status_badge(str(r.get("status") or ""))}</td>'
            f'<td>{html.escape(str(r.get("match_ok") if r.get("match_ok") is not None else ""))}</td>'
            f'<td><code>{html.escape(str(r.get("register_id") or ""))}</code></td>'
            f'<td>{html.escape(str(r.get("case_id") or ""))}</td>'
            f'<td><code>{html.escape(str(r.get("created_at") or ""))}</code></td>'
            "</tr>"
        )
    tbody = "".join(rows) or '<tr><td colspan="10">(none)</td></tr>'
    body = f"""
{nav_html("pair")}
<h1>Pair QC · MCP SSOT 🆚 UI</h1>
<p class="meta">Pipeline <code>E_pair_qc</code> · gate=<code>never</code> · schema={html.escape(schema_s)} ·
detect only (not auto-fix) · <a href="/api/pair">/api/pair</a></p>
{err_html}{flash_html}
<p class="meta"><b>Why:</b> API success can hide UI bugs. Two independent paths + normalize before compare.
Fail → case with both evidences + register_id + tdd_rule_id.</p>
<div class="grid2">
  <div class="panel">
    <h2>Run offline pair (L1)</h2>
    <p class="meta">example phone: MCP <code>+86 138-0013-8000</code> vs UI <code>138-0013-8000</code> → pass after normalize</p>
    <div class="form-row"><label>field <input id="pq_field" value="phone"/></label>
    <label>region <input id="pq_region" value="CN"/></label></div>
    <div class="form-row"><label>register_id <input id="pq_reg" placeholder="optional" size="28"/></label></div>
    <div class="form-row"><label>MCP value (backend SSOT)<br/>
    <input id="pq_mcp" style="width:100%" value="+86 138-0013-8000"/></label></div>
    <div class="form-row"><label>UI value (screen extract)<br/>
    <input id="pq_ui" style="width:100%" value="138-0013-8000"/></label></div>
    <p><button type="button" onclick="pqRun()">Compare paths</button>
    <button type="button" onclick="location.reload()">Refresh</button></p>
  </div>
  <div class="panel">
    <h2>Summary</h2>
    <ul>
      <li>listed: <b>{int(counts.get("listed") or 0)}</b></li>
      <li>pass: <b>{int(counts.get("pass") or 0)}</b></li>
      <li>fail: <b>{int(counts.get("fail") or 0)}</b></li>
      <li>error: <b>{int(counts.get("error") or 0)}</b></li>
    </ul>
    <h3>Flow</h3>
    <ol class="meta">{flow_ol or "<li>normalize → compare → case</li>"}</ol>
    <ul class="meta">
      <li>normalize kills false-positive format noise</li>
      <li>fail opens <code>fault_event</code> pair_qc_mismatch</li>
      <li>never flips schema_qc match_ok</li>
    </ul>
  </div>
</div>
<div class="panel">
<h2>Recent runs</h2>
<div class="wrap"><table>
<thead><tr>
<th>id</th><th>field</th><th>region</th><th>mcp_norm</th><th>ui_norm</th>
<th>status</th><th>match</th><th>register_id</th><th>case</th><th>created</th>
</tr></thead>
<tbody>{tbody}</tbody></table></div>
</div>
<script>
async function pqRun(){{
  const body={{
    field: document.getElementById('pq_field').value,
    region: document.getElementById('pq_region').value,
    register_id: document.getElementById('pq_reg').value,
    mcp_value: document.getElementById('pq_mcp').value,
    ui_value: document.getElementById('pq_ui').value
  }};
  const r=await fetch('/api/pair/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
  const j=await r.json();
  if(!r.ok){{ alert(j.error||JSON.stringify(j)); return; }}
  const msg='status='+(j.status||'')+' match='+(j.match_ok)+' run='+(j.run_id||'')+' case='+((j.case&&j.case.case_id)||'');
  location.href='/pair?flash='+encodeURIComponent(msg);
}}
</script>
"""
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>Pair QC</title>
<style>{SHARED_CSS}
.ok{{color:#8dffb5}}
ul,ol{{line-height:1.55}}
input,textarea{{background:#0d1117;color:#e6edf3;border:1px solid #333;padding:.25rem .4rem;border-radius:4px}}
button{{cursor:pointer;margin-right:.35rem}}
</style></head><body>
{body}
</body></html>"""


def hko_page_html(
    dash: dict | None,
    *,
    err: str | None = None,
    flash: str | None = None,
) -> str:
    """HKO dual-path weather proof UI (Playwright vs OpenClaw). gate=never."""
    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    flash_html = f'<p class="ok">{html.escape(flash)}</p>' if flash else ""
    dash = dash or {}
    counts = dash.get("counts") or {}
    runs = dash.get("runs") or []
    latest = dash.get("latest") or {}
    contracts = dash.get("contracts") or {}
    url = html.escape(str(dash.get("url") or contracts.get("url") or ""))
    table_html = dash.get("table_html") or ""
    if not table_html and hko_render_html_table is not None and latest:
        try:
            table_html = hko_render_html_table(latest)
        except Exception as e:
            table_html = f'<p class="err">table: {html.escape(type(e).__name__)}</p>'
    if not table_html:
        table_html = '<p class="meta">(no runs yet — click Run proof)</p>'

    pw = latest.get("playwright") or {}
    oc = latest.get("openclaw") or {}
    pw_name = ""
    oc_name = ""
    try:
        from pathlib import Path as _P

        if pw.get("screenshot_path"):
            pw_name = _P(str(pw["screenshot_path"])).name
        if oc.get("screenshot_path"):
            oc_name = _P(str(oc["screenshot_path"])).name
    except Exception:
        pass
    thumbs = []
    if pw_name:
        thumbs.append(
            f'<figure><figcaption>Playwright</figcaption>'
            f'<a href="/hko/img?f={html.escape(pw_name)}" target="_blank">'
            f'<img src="/hko/img?f={html.escape(pw_name)}" alt="pw" '
            f'style="max-width:100%;max-height:280px;border:1px solid #333"/></a>'
            f"</figure>"
        )
    if oc_name:
        thumbs.append(
            f'<figure><figcaption>OpenClaw</figcaption>'
            f'<a href="/hko/img?f={html.escape(oc_name)}" target="_blank">'
            f'<img src="/hko/img?f={html.escape(oc_name)}" alt="oc" '
            f'style="max-width:100%;max-height:280px;border:1px solid #333"/></a>'
            f"</figure>"
        )
    thumbs_html = "".join(thumbs) or '<p class="meta">(no screenshots)</p>'

    run_rows = []
    for r in runs:
        run_rows.append(
            "<tr>"
            f'<td><code>{html.escape(str(r.get("run_id") or ""))}</code></td>'
            f'<td>{html.escape(str(r.get("created_at") or ""))}</td>'
            f'<td>{html.escape(str(r.get("ok")))}</td>'
            f'<td>{html.escape(str(r.get("match_ok")))}</td>'
            f'<td>{html.escape(str(r.get("playwright_ok")))}</td>'
            f'<td>{html.escape(str(r.get("openclaw_ok")))}</td>'
            f'<td class="meta">{html.escape(str(r.get("error") or ""))}</td>'
            "</tr>"
        )
    runs_tbody = "".join(run_rows) or '<tr><td colspan="7">(none)</td></tr>'

    body = f"""
{nav_html("hko")}
<h1>HKO Proof · Playwright vs OpenClaw</h1>
<p class="meta">Pipeline <code>HKO_dual_path_proof</code> · gate=<code>never</code> ·
<a href="{url}" target="_blank" rel="noopener">{url}</a> ·
<a href="/api/hko">/api/hko</a></p>
{err_html}{flash_html}
<p class="meta"><b>Why:</b> Primary = Playwright full-page + DOM. Secondary = traced webbrowser.open +
screen.snapshot. Ollama extracts time/region/temp; normalize before compare. Detect only.</p>
<div class="grid2">
  <div class="panel">
    <h2>Run dual-path proof</h2>
    <div class="form-row"><label><input type="checkbox" id="hko_headed"/> headed Chromium</label></div>
    <div class="form-row"><label>OpenClaw delay (s)
    <input id="hko_delay" type="number" step="0.5" value="4" style="width:5rem"/></label></div>
    <div class="form-row"><label><input type="checkbox" id="hko_skip_pw"/> skip Playwright</label>
    <label><input type="checkbox" id="hko_skip_oc"/> skip OpenClaw</label>
    <label><input type="checkbox" id="hko_skip_ol"/> skip Ollama</label></div>
    <p><button type="button" onclick="hkoRun()">Run proof</button>
    <button type="button" onclick="location.reload()">Refresh</button></p>
    <p class="meta">Live run can take minutes (browser + MCP + dual vision). Soft-fails if deps down.</p>
  </div>
  <div class="panel">
    <h2>Summary</h2>
    <ul>
      <li>listed: <b>{int(counts.get("listed") or 0)}</b></li>
      <li>ok: <b>{int(counts.get("ok") or 0)}</b></li>
      <li>match: <b>{int(counts.get("match") or 0)}</b></li>
      <li>latest run: <code>{html.escape(str(latest.get("run_id") or "—"))}</code></li>
      <li>latest match_ok: <b>{html.escape(str((latest.get("compare") or {}).get("match_ok")))}</b></li>
    </ul>
    <ul class="meta">
      <li>Playwright = primary authority for page DOM</li>
      <li>OpenClaw = desktop snapshot secondary (no MCP browser.open)</li>
      <li>never flips schema_qc match_ok</li>
    </ul>
  </div>
</div>
<div class="panel">
<h2>Latest compare · Field | Playwright | OpenClaw | Match</h2>
<div class="wrap">{table_html}</div>
</div>
<div class="panel grid2">
{thumbs_html}
</div>
<div class="panel">
<h2>Recent runs</h2>
<div class="wrap"><table>
<thead><tr>
<th>run_id</th><th>created</th><th>ok</th><th>match</th><th>pw</th><th>oc</th><th>error</th>
</tr></thead>
<tbody>{runs_tbody}</tbody></table></div>
</div>
<script>
async function hkoRun(){{
  const body={{
    headed: document.getElementById('hko_headed').checked,
    delay: parseFloat(document.getElementById('hko_delay').value||'4'),
    skip_playwright: document.getElementById('hko_skip_pw').checked,
    skip_openclaw: document.getElementById('hko_skip_oc').checked,
    skip_ollama: document.getElementById('hko_skip_ol').checked
  }};
  const btn=document.querySelector('button[onclick="hkoRun()"]');
  if(btn){{btn.disabled=true; btn.textContent='Running…';}}
  try{{
    const r=await fetch('/api/hko/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
    const j=await r.json();
    if(!r.ok){{ alert(j.error||JSON.stringify(j)); return; }}
    const run=j.run||{{}};
    const cmp=run.compare||{{}};
    const msg='run='+(run.run_id||'')+' ok='+run.ok+' match='+cmp.match_ok+' elapsed='+(run.elapsed_s||'');
    location.href='/hko?flash='+encodeURIComponent(msg);
  }}catch(e){{
    alert(String(e));
  }}finally{{
    if(btn){{btn.disabled=false; btn.textContent='Run proof';}}
  }}
}}
</script>
"""
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>HKO Proof</title>
<style>{SHARED_CSS}
.ok{{color:#8dffb5}}
ul,ol{{line-height:1.55}}
figure{{margin:0}}
figcaption{{color:#8b9bb4;font-size:12px;margin-bottom:.35rem}}
input,textarea{{background:#0d1117;color:#e6edf3;border:1px solid #333;padding:.25rem .4rem;border-radius:4px}}
button{{cursor:pointer;margin-right:.35rem}}
</style></head><body>
{body}
</body></html>"""


def status_badge(status: str) -> str:
    s = html.escape(status or "")
    return f'<span class="badge {s}">{s}</span>'


def page_html(tables, selected, data, err, schema=None) -> str:
    opts = []
    for t in tables:
        sel = " selected" if t == selected else ""
        opts.append(
            f'<option value="{html.escape(t)}"{sel}>{html.escape(t)}</option>'
        )
    body_table = ""
    if err:
        body_table = f'<p class="err">{html.escape(err)}</p>'
    elif data:
        th = "".join(f"<th>{html.escape(c)}</th>" for c in data["columns"])
        trs = []
        for row in data["rows"]:
            tds = "".join(
                f"<td>{html.escape('' if v is None else str(v))}</td>" for v in row
            )
            trs.append(f"<tr>{tds}</tr>")
        body_rows = "".join(trs) or '<tr><td colspan="99">(empty)</td></tr>'
        schema_block = ""
        if schema:
            sch_rows = []
            for c in schema["columns"]:
                sch_rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(c['name']))}</td>"
                    f"<td>{html.escape(str(c['type']))}</td>"
                    f"<td>{'Y' if c['notnull'] else ''}</td>"
                    f"<td>{c['pk'] or ''}</td>"
                    f"<td>{html.escape('' if c['dflt_value'] is None else str(c['dflt_value']))}</td>"
                    "</tr>"
                )
            schema_block = f"""
            <h2>Schema (PRAGMA) · {schema['column_count']} fields · rows={schema['row_count']}</h2>
            <p class="meta">QC: <code>{html.escape(schema.get('api_schema_url',''))}</code></p>
            <div class="wrap"><table><thead><tr>
              <th>name</th><th>type</th><th>notnull</th><th>pk</th><th>default</th>
            </tr></thead><tbody>{''.join(sch_rows)}</tbody></table></div>
            """
        body_table = f"""
        {schema_block}
        <h2>Data</h2>
        <p>table=<b>{html.escape(data['table'])}</b> ·
           showing {len(data['rows'])} / {data['count']} ·
           limit={data['limit']}</p>
        <div class="wrap"><table><thead><tr>{th}</tr></thead>
        <tbody>{body_rows}</tbody></table></div>
        """
    sel_meta = (
        f' · selected: <code>{html.escape(selected)}</code>' if selected else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>agent.db browser</title>
<style>{SHARED_CSS}</style></head><body>
{nav_html("browser")}
<h1>agent.db browser</h1>
<p class="meta">Local only · DB: <code>{html.escape(str(DB_PATH))}</code><br/>
URL: <code>http://{HOST}:{PORT}/</code>{sel_meta}</p>
<form method="get" action="/">
  <label>Table
    <select name="table" required>
      <option value="">-- select --</option>
      {''.join(opts)}
    </select>
  </label>
  <label>Limit <input type="number" name="limit" value="{DEFAULT_LIMIT}" min="1" max="{MAX_LIMIT}"/></label>
  <button type="submit">Submit</button>
</form>
<p class="meta">API: <code>/api/tables</code> · <code>/api/table/<name></code> · <code>/api/schema/<name></code></p>
{body_table}
</body></html>"""


def _opt_list(rows, id_key, label_fn, selected=None) -> str:
    parts = ['<option value="">--</option>']
    for r in rows:
        rid = r[id_key]
        sel = " selected" if selected is not None and int(rid) == int(selected) else ""
        parts.append(
            f'<option value="{int(rid)}"{sel}>{html.escape(label_fn(r))}</option>'
        )
    return "".join(parts)


def _render_task_rows_html(tasks: list[dict], selected_id: int | None) -> str:
    """Flat indented rows from parent_task_id."""
    by_parent: dict = {}
    for t in tasks:
        pid = t.get("parent_task_id")
        by_parent.setdefault(pid, []).append(t)

    ids = {int(t["id"]) for t in tasks}
    roots = [
        t
        for t in tasks
        if t.get("parent_task_id") is None or int(t["parent_task_id"]) not in ids
    ]

    lines: list[str] = []
    seen: set[int] = set()

    def walk_node(t: dict, depth: int) -> None:
        tid = int(t["id"])
        if tid in seen:
            return
        seen.add(tid)
        if depth <= 0:
            cls = "tree-root"
        elif depth == 1:
            cls = "tree-child"
        else:
            cls = "tree-child2"
        if selected_id is not None and tid == int(selected_id):
            cls += " sel-row"
        indent = "\u21b3 " if depth else ""
        label = html.escape(str(t.get("task_label") or ""))
        title = html.escape(str(t.get("title") or ""))
        action = html.escape(str(t.get("action_code") or ""))
        ch = html.escape(str(t.get("channel_code") or ""))
        mo = html.escape(str(t.get("module_code") or ""))
        ver = html.escape(str(t.get("version_label") or ""))
        href = f"/tasks?task_id={tid}"
        lines.append(
            f'<tr class="{cls}">'
            f'<td>{indent}<a href="{href}"><code>{label}</code></a></td>'
            f"<td>{title}</td>"
            f"<td><code>{action}</code></td>"
            f"<td>{status_badge(str(t.get('status') or ''))}</td>"
            f"<td>{ch} / {mo} / {ver}</td>"
            f'<td><a href="{href}">detail</a></td>'
            f"</tr>"
        )
        for ch_t in by_parent.get(tid, []):
            walk_node(ch_t, depth + 1)

    for t in roots:
        walk_node(t, 0)
    for t in tasks:
        if int(t["id"]) not in seen:
            walk_node(t, 0)

    if not lines:
        return '<tr><td colspan="6">(no tasks)</td></tr>'
    return "".join(lines)


def tasks_page_html(
    dims: dict,
    tree_data: dict,
    detail: dict | None,
    filters: dict,
    err: str | None = None,
    flash: str | None = None,
) -> str:
    channels = dims.get("channels") or []
    modules = dims.get("modules") or []
    versions = dims.get("versions") or []
    actions = dims.get("actions") or []
    tdd_types = dims.get("tdd_types") or []
    tasks = tree_data.get("tasks") or []
    selected_id = filters.get("task_id")

    ch_opts = _opt_list(
        channels, "id", lambda r: f"{r['code']} ({r['name']})", filters.get("channel_id")
    )
    mo_opts = _opt_list(
        modules, "id", lambda r: f"{r['code']} ({r['name']})", filters.get("module_id")
    )
    ver_opts = _opt_list(
        versions,
        "id",
        lambda r: f"{r['version_label']} · {r['channel_code']}/{r['module_code']}",
        filters.get("version_id"),
    )
    status_sel = filters.get("status") or ""
    st_opts = ['<option value="">-- all --</option>']
    for s in ("pending", "running", "pass", "fail", "cancelled"):
        sel = " selected" if status_sel == s else ""
        st_opts.append(f'<option value="{s}"{sel}>{s}</option>')

    tree_rows = _render_task_rows_html(tasks, selected_id)

    detail_html = '<p class="meta">Select a task from the tree.</p>'
    if detail:
        t = detail["task"]
        payload_pre = html.escape(
            json.dumps(detail.get("payload") or {}, ensure_ascii=False, indent=2)
        )
        open_links = []
        if detail.get("open_qc_url"):
            open_links.append(
                f'<a href="{html.escape(detail["open_qc_url"])}" target="_blank">Open QC browser</a>'
            )
        if detail.get("open_schema_api_url"):
            open_links.append(
                f'<a href="{html.escape(detail["open_schema_api_url"])}" target="_blank">Open /api/schema</a>'
            )
        if detail.get("table_name"):
            open_links.append(
                f'<a href="/?table={html.escape(str(detail["table_name"]))}&limit=200">DB Browser table</a>'
            )
        links_s = " · ".join(open_links) if open_links else "(no QC links)"

        action_code = str(t.get("action_code") or "")
        run_btns = []
        if action_code in ("qc.verify_schema", "schema.remediate") or detail.get("table_name"):
            run_btns.append(
                f'<button type="button" onclick="runQc({int(t["id"])}, false)">Run QC</button>'
            )
            run_btns.append(
                f'<button type="button" onclick="runQc({int(t["id"])}, true)">Run QC + MCP</button>'
            )
        if action_code in ("schema.remediate", "qc.verify_schema") or (
            isinstance(detail.get("payload"), dict)
            and (detail["payload"].get("fault_event_id") or detail["payload"].get("goal_type"))
        ):
            run_btns.append(
                f'<button type="button" onclick="runSupervise({int(t["id"])}, false)">Run fail-handling</button>'
            )
            run_btns.append(
                f'<button type="button" onclick="runSupervise({int(t["id"])}, true)">Run FH + Ollama</button>'
            )
        run_panel = (
            f'<p class="meta">Phase5 actions · gate stays PRAGMA-only</p><p>{" ".join(run_btns)}</p>'
            if run_btns
            else ""
        )

        child_rows = []
        for c in detail.get("children") or []:
            child_rows.append(
                "<tr>"
                f'<td><a href="/tasks?task_id={c["id"]}"><code>{html.escape(str(c["task_label"]))}</code></a></td>'
                f'<td>{html.escape(str(c["title"]))}</td>'
                f'<td><code>{html.escape(str(c.get("action_code") or ""))}</code></td>'
                f'<td>{status_badge(str(c.get("status") or ""))}</td>'
                "</tr>"
            )
        children_block = (
            f'<div class="wrap"><table><thead><tr><th>label</th><th>title</th><th>action</th><th>status</th></tr></thead>'
            f'<tbody>{"".join(child_rows) or "<tr><td colspan=4>(none)</td></tr>"}</tbody></table></div>'
        )

        field_src = detail.get("fields") or detail.get("child_fields") or []
        # prefer own fields; if empty show child_fields
        if not detail.get("fields") and detail.get("child_fields"):
            field_src = detail["child_fields"]
        f_rows = []
        for f in field_src:
            f_rows.append(
                "<tr>"
                f'<td>{html.escape(str(f.get("field_name") or ""))}</td>'
                f'<td><code>{html.escape(str(f.get("tdd_code") or ""))}</code></td>'
                f'<td>{f.get("nullable")}</td>'
                f'<td>{f.get("is_pk")}</td>'
                f'<td>{f.get("sort_order")}</td>'
                f'<td>task#{f.get("task_id")}</td>'
                "</tr>"
            )
        fields_block = (
            f'<div class="wrap"><table><thead><tr>'
            f"<th>field</th><th>tdd</th><th>null</th><th>pk</th><th>sort</th><th>task</th>"
            f"</tr></thead><tbody>{''.join(f_rows) or '<tr><td colspan=6>(no fields)</td></tr>'}</tbody></table></div>"
        )

        qc_rows = []
        for q in detail.get("qc_runs") or []:
            qc_rows.append(
                "<tr>"
                f'<td>{q.get("id")}</td>'
                f'<td>{html.escape(str(q.get("table_name") or ""))}</td>'
                f'<td>{"Y" if q.get("match_ok") else "N"}</td>'
                f'<td>{html.escape(str(q.get("summary") or "")[:120])}</td>'
                f'<td>{html.escape(str(q.get("created_at") or ""))}</td>'
                "</tr>"
            )
        qc_block = (
            f'<div class="wrap"><table><thead><tr>'
            f"<th>id</th><th>table</th><th>match</th><th>summary</th><th>at</th>"
            f"</tr></thead><tbody>{''.join(qc_rows) or '<tr><td colspan=5>(no qc runs)</td></tr>'}</tbody></table></div>"
        )

        ssot_rows = []
        for s in detail.get("schema_ssot") or []:
            ssot_rows.append(
                "<tr>"
                f'<td><code>{html.escape(str(s.get("keyword") or ""))}</code></td>'
                f'<td>{html.escape(str(s.get("value_text") or "")[:200])}</td>'
                f'<td>{html.escape(str(s.get("source") or ""))}</td>'
                "</tr>"
            )
        ssot_block = (
            f'<div class="wrap"><table><thead><tr><th>keyword</th><th>value</th><th>source</th></tr></thead>'
            f'<tbody>{"".join(ssot_rows) or "<tr><td colspan=3>(no ssot)</td></tr>"}</tbody></table></div>'
        )

        # FH2 task_ssot multi-dim capability panel
        ts_rows_html = []
        for s in detail.get("task_ssot") or []:
            ts_rows_html.append(
                "<tr>"
                f'<td>{html.escape(str(s.get("id") or ""))}</td>'
                f'<td><code>{html.escape(str(s.get("dim_key") or ""))}</code></td>'
                f'<td>{html.escape(str(s.get("value_text") or "")[:220])}</td>'
                f'<td>{html.escape(str(s.get("value_type") or ""))}</td>'
                f'<td>{html.escape(str(s.get("source") or ""))}</td>'
                f'<td>{html.escape(str(s.get("notes") or "")[:120])}</td>'
                f'<td><button type="button" onclick="delTaskSsot({int(t["id"])},{int(s["id"])})">del</button></td>'
                "</tr>"
            )
        task_ssot_block = f"""
        <div class="panel" style="border-color:#2a5a4a">
          <h2>Task SSOT (multi-dim · FH2)</h2>
          <p class="meta">Capability / planning dims — not a structure gate. UNIQUE(task_id, dim_key).</p>
          <div class="wrap"><table><thead><tr>
            <th>id</th><th>dim_key</th><th>value</th><th>type</th><th>source</th><th>notes</th><th></th>
          </tr></thead>
          <tbody>{''.join(ts_rows_html) or '<tr><td colspan=7>(no task_ssot rows)</td></tr>'}</tbody></table></div>
          <h3>Upsert dim</h3>
          <form onsubmit="return postTaskSsot(event)">
            <input type="hidden" name="task_id" value="{int(t['id'])}"/>
            <div class="form-row">
              <label>dim_key <input name="dim_key" required placeholder="tool.capability.open_browser" style="width:18rem"/></label>
              <label>value <input name="value_text" style="width:14rem"/></label>
              <label>type
                <select name="value_type">
                  <option>string</option><option>bool</option><option>number</option>
                  <option>json</option>
                </select>
              </label>
              <label>source <input name="source" value="manual" style="width:6rem"/></label>
              <label>sort <input name="sort_order" type="number" value="0" style="width:4rem"/></label>
            </div>
            <div class="form-row">
              <label>notes <input name="notes" style="width:70%"/></label>
              <button type="submit">Upsert dim</button>
            </div>
          </form>
        </div>
        """

        parent = t.get("parent_task_id")
        parent_link = (
            f'<a href="/tasks?task_id={int(parent)}">#{int(parent)}</a>'
            if parent is not None
            else "NULL (root)"
        )

        # FH1 goal panel (Fail-Handling; not a gate)
        goal = detail.get("goal") or {}
        goal_block = ""
        if goal:
            steps = goal.get("plan_steps") or []
            steps_li = "".join(
                f"<li>{html.escape(str(s))}</li>" for s in steps
            ) or "<li class='meta'>(empty - run STEP4 assemble)</li>"
            std_rows = goal.get("step1_std_rows") or []
            std_tr = []
            for r in std_rows[:40]:
                if not isinstance(r, dict):
                    continue
                std_tr.append(
                    "<tr>"
                    f'<td><code>{html.escape(str(r.get("dim_key") or ""))}</code></td>'
                    f'<td>{html.escape(str(r.get("op") or ""))}</td>'
                    f'<td>{html.escape(str(r.get("dim_value"))[:80])}</td>'
                    f'<td>{html.escape(str(r.get("expected_value"))[:80])}</td>'
                    f'<td>{html.escape(str(r.get("source") or ""))}</td>'
                    "</tr>"
                )
            std_tbl = (
                '<div class="wrap"><table><thead><tr>'
                "<th>dim_key</th><th>op</th><th>value</th><th>expected</th><th>source</th>"
                f"</tr></thead><tbody>{''.join(std_tr) or '<tr><td colspan=5>(none)</td></tr>'}"
                "</tbody></table></div>"
            )
            d4 = goal.get("step4_delta_rows") or []
            d4_tr = []
            for r in d4[:50]:
                if not isinstance(r, dict):
                    continue
                d4_tr.append(
                    "<tr>"
                    f'<td><code>{html.escape(str(r.get("dim_key") or ""))}</code></td>'
                    f'<td><b>{html.escape(str(r.get("op") or ""))}</b></td>'
                    f'<td>{html.escape(str(r.get("dim_value"))[:80])}</td>'
                    f'<td>{html.escape(str(r.get("expected_value"))[:80])}</td>'
                    f'<td>{html.escape(str(r.get("target_store") or ""))}</td>'
                    f'<td>{html.escape(str(r.get("note") or "")[:100])}</td>'
                    "</tr>"
                )
            d4_tbl = (
                '<div class="wrap"><table><thead><tr>'
                "<th>dim_key</th><th>op</th><th>have</th><th>want</th><th>store</th><th>note</th>"
                f"</tr></thead><tbody>{''.join(d4_tr) or '<tr><td colspan=6>(no STEP4 deltas yet)</td></tr>'}"
                "</tbody></table></div>"
            )
            counts = goal.get("step4_counts") or {}
            counts_s = html.escape(
                f"add={counts.get('add', 0)} update={counts.get('update', 0)} "
                f"del={counts.get('del', 0)} keep={counts.get('keep', 0)}"
            )
            s2 = goal.get("step2_std_rows") or []
            s2_tr = []
            for r in s2[:40]:
                if not isinstance(r, dict):
                    continue
                s2_tr.append(
                    "<tr>"
                    f'<td><code>{html.escape(str(r.get("dim_key") or ""))}</code></td>'
                    f'<td>{html.escape(str(r.get("dim_value"))[:120])}</td>'
                    f'<td>{html.escape(str(r.get("source") or ""))}</td>'
                    f'<td>{html.escape(str(r.get("evidence_ref") or "")[:60])}</td>'
                    "</tr>"
                )
            s2_tbl = (
                '<div class="wrap"><table><thead><tr>'
                "<th>dim_key</th><th>value</th><th>source</th><th>evidence</th>"
                f"</tr></thead><tbody>{''.join(s2_tr) or '<tr><td colspan=4>(no STEP2 vision rows yet)</td></tr>'}"
                "</tbody></table></div>"
            )
            s2_meta = html.escape(
                f"model={goal.get('step2_model') or '-'} · "
                f"vision_id={goal.get('step2_vision_id') or '-'} · "
                f"pending={goal.get('step2_pending')} · "
                f"err={goal.get('step2_error') or '-'}"
            )
            goal_block = f"""
            <div class="panel" style="border-color:#6a4a2a">
              <h2>Goal (Fail-Handling FH1)</h2>
              <p class="meta">pipeline={html.escape(str(goal.get('pipeline') or 'B_fail_handling'))}
                 · source={html.escape(str(goal.get('goal_source') or ''))}</p>
              <p><b>goal_type</b>: <code>{html.escape(str(goal.get('goal_type') or ''))}</code></p>
              <p><b>goal_text</b>: {html.escape(str(goal.get('goal_text') or ''))}</p>
              <p><b>success_criteria</b>: {html.escape(str(goal.get('success_criteria') or ''))}</p>
              <h3>plan_steps</h3>
              <ul>{steps_li}</ul>
              <h3>STEP2 vision (FH4 · Ollama · not a gate)</h3>
              <p class="meta">{html.escape(str(goal.get('step2_summary') or ''))} · {s2_meta}</p>
              {s2_tbl}
              <p>
                <button type="button" onclick="runVision({int(t['id'])}, false)">Run STEP2 Ollama</button>
                <button type="button" onclick="runVision({int(t['id'])}, true)">STEP2 resolve-only</button>
              </p>
              <h3>STEP4 SSOT deltas (FH3 · not a gate)</h3>
              <p class="meta">{html.escape(str(goal.get('step4_summary') or ''))} · {counts_s}</p>
              {d4_tbl}
              <p><button type="button" onclick="runAssemble({int(t['id'])})">Re-run STEP4 assemble</button></p>
              <h3>STEP5 report (FH5 · not a gate)</h3>
              <p class="meta">report_id={html.escape(str(goal.get('step5_report_id') or '-'))}
                 · notified={html.escape(str(goal.get('step5_notified_at') or '-'))}</p>
              <p>{html.escape(str(goal.get('step5_summary') or '(no report yet)'))}</p>
              <pre style="white-space:pre-wrap;max-height:240px;overflow:auto;background:#121820;padding:.6rem;border:1px solid #333">{html.escape(str(goal.get('step5_markdown') or '')[:4000])}</pre>
              <p>
                <button type="button" onclick="runReport({int(t['id'])}, false)">Build STEP5 report</button>
                <button type="button" onclick="runReport({int(t['id'])}, true)">Build + notify</button>
              </p>
              <h3>FH6 option SSOT match (not a gate)</h3>
              <p class="meta">status={html.escape(str(goal.get('step6_match_status') or '-'))}
                 · score={html.escape(str(goal.get('step6_match_score') or '-'))}
                 · option={html.escape(str(goal.get('step6_option_code') or goal.get('step6_option_id') or '-'))}
                 · solution_id={html.escape(str(goal.get('step6_solution_id') or '-'))}</p>
              <p>{html.escape(str(goal.get('step6_summary') or '(no match yet)'))}</p>
              <p><button type="button" onclick="runMatch({int(t['id'])})">Run FH6 SSOT match</button></p>
              <h3>Phase5 supervisor (not a gate)</h3>
              <p class="meta">ran_at={html.escape(str(goal.get('supervisor_ran_at') or '-'))}</p>
              <p>{html.escape(str(goal.get('supervisor_summary') or '(not run yet)'))}</p>
              <p>
                <button type="button" onclick="runSupervise({int(t['id'])}, false)">Run supervise</button>
                <button type="button" onclick="runSupervise({int(t['id'])}, true)">Supervise + Ollama</button>
              </p>
              <h3>STEP1 std rows (observation)</h3>
              {std_tbl}
            </div>
            """

        # add field form if field action
        add_field_form = ""
        if str(t.get("action_code") or "").startswith("field."):
            tdd_opts = _opt_list(tdd_types, "id", lambda r: f"{r['code']} — {r['name']}")
            add_field_form = f"""
            <div class="panel">
              <h2>Add field</h2>
              <form id="form-add-field" onsubmit="return postAddField(event)">
                <input type="hidden" name="task_id" value="{int(t['id'])}"/>
                <div class="form-row"><label>name <input name="field_name" required/></label>
                <label>tdd <select name="tdd_type_id" required>{tdd_opts}</select></label>
                <label>nullable <input type="number" name="nullable" value="1" min="0" max="1"/></label>
                <label>pk <input type="number" name="is_pk" value="0" min="0" max="1"/></label>
                <label>sort <input type="number" name="sort_order" value="0"/></label>
                <button type="submit">Add field</button></div>
              </form>
            </div>
            """

        writer_val = html.escape(str(t.get("writer") or ""))
        session_val = html.escape(str(t.get("session_id") or ""))
        prompt_analyze_url = (
            f"http://127.0.0.1:18765/prompt-analyze?task_id={int(t['id'])}"
        )
        context_block = f"""
            <div class="panel">
              <h2>Prompt context (writer / session)</h2>
              <p class="meta">Required by LLM Prompt Improve on :18765 · task_id is this row id</p>
              <form onsubmit="return postTaskContext(event)">
                <input type="hidden" name="task_id" value="{int(t['id'])}"/>
                <div class="form-row">
                  <label>writer <input name="writer" value="{writer_val}" placeholder="e.g. alice" style="width:12em"/></label>
                  <label>session_id <input name="session_id" value="{session_val}" placeholder="e.g. sess_…" style="width:16em"/></label>
                  <button type="submit">Save context</button>
                  <a href="{html.escape(prompt_analyze_url)}" target="_blank" rel="noopener">Open Prompt Analyze</a>
                </div>
              </form>
            </div>
            """
        detail_html = f"""
        <div class="panel">
          <h2>Task detail · <code>{html.escape(str(t.get('task_label')))}</code>
              {status_badge(str(t.get('status') or ''))}</h2>
          <p><b>{html.escape(str(t.get('title') or ''))}</b></p>
          <p class="meta">
            id={int(t['id'])} · parent={parent_link} ·
            action=<code>{html.escape(str(t.get('action_code') or ''))}</code> ·
            {html.escape(str(t.get('channel_code')))}/{html.escape(str(t.get('module_code')))}/
            v{html.escape(str(t.get('version_label')))} ·
            writer=<code>{writer_val or '-'}</code> ·
            session_id=<code>{session_val or '-'}</code>
          </p>
          <p>{links_s}</p>
          {context_block}
          {run_panel}
          {goal_block}
          {task_ssot_block}
          <h2>Payload</h2>
          <pre>{payload_pre}</pre>
          <h2>Children (parent_task_id)</h2>
          {children_block}
          <h2>Fields (TDD)</h2>
          {fields_block}
          {add_field_form}
          <h2>QC runs</h2>
          {qc_block}
          <h2>Schema SSOT</h2>
          {ssot_block}
        </div>
        """

    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    flash_html = f'<p class="ok">{html.escape(flash)}</p>' if flash else ""

    # create forms
    act_opts = _opt_list(
        actions, "id", lambda r: f"{r['code']} — {r['name']}"
    )
    ver_opts_form = _opt_list(
        versions,
        "id",
        lambda r: f"{r['version_label']} · {r['channel_code']}/{r['module_code']} (id={r['id']})",
    )
    ch_opts_form = _opt_list(channels, "id", lambda r: f"{r['code']} ({r['id']})")
    mo_opts_form = _opt_list(modules, "id", lambda r: f"{r['code']} ({r['id']})")
    tdd_opts_form = "".join(
        f'<option value="{html.escape(r["code"])}">{html.escape(r["code"])}</option>'
        for r in tdd_types
    )

    create_panel = f"""
    <div class="grid2">
      <div class="panel">
        <h2>Create version</h2>
        <form onsubmit="return postVersion(event)">
          <div class="form-row"><label>channel <select name="channel_id" required>{ch_opts_form}</select></label></div>
          <div class="form-row"><label>module <select name="module_id" required>{mo_opts_form}</select></label></div>
          <div class="form-row"><label>label <input name="version_label" placeholder="1.2" required/></label>
            <label>status
              <select name="status"><option>draft</option><option selected>active</option>
              <option>released</option><option>deprecated</option></select>
            </label></div>
          <div class="form-row"><label>title <input name="title" style="width:80%"/></label></div>
          <div class="form-row"><label>notes <input name="notes" style="width:80%"/></label></div>
          <button type="submit">Create version</button>
        </form>
      </div>
      <div class="panel">
        <h2>Create task bundle</h2>
        <p class="meta">Root table.create + {{label}}a fields + {{label}}b QC. No DDL applied.</p>
        <form onsubmit="return postBundle(event)">
          <div class="form-row"><label>channel <select name="channel_id" required>{ch_opts_form}</select></label>
            <label>module <select name="module_id" required>{mo_opts_form}</select></label></div>
          <div class="form-row"><label>version <select name="version_id" required>{ver_opts_form}</select></label></div>
          <div class="form-row"><label>root label <input name="root_label" placeholder="1.2" required/></label>
            <label>table <input name="table_name" placeholder="demo_x" required/></label></div>
          <div class="form-row
          <label>writer <input name="writer" placeholder="optional"/></label>
          <label>session_id <input name="session_id" placeholder="optional"/></label>
        </div>
        <div class="form-row">"><label>title <input name="title" style="width:80%" placeholder="create table demo_x" required/></label></div>
          <div class="form-row"><label>fields JSON
            <textarea name="fields_json" rows="5" style="width:95%;font-family:monospace">[
  {{"name":"id","tdd_code":"int","nullable":0,"is_pk":1,"sort_order":0}},
  {{"name":"name","tdd_code":"text","nullable":0,"is_pk":0,"sort_order":1}}
]</textarea></label></div>
          <button type="submit">Create bundle</button>
        </form>
      </div>
    </div>
    <div class="panel">
      <h2>Create single task</h2>
      <form onsubmit="return postTask(event)">
        <div class="form-row">
          <label>channel <select name="channel_id" required>{ch_opts_form}</select></label>
          <label>module <select name="module_id" required>{mo_opts_form}</select></label>
          <label>version <select name="version_id" required>{ver_opts_form}</select></label>
          <label>action <select name="action_name_id" required>{act_opts}</select></label>
        </div>
        <div class="form-row">
          <label>label <input name="task_label" required placeholder="1.2c"/></label>
          <label>title <input name="title" required style="width:40%"/></label>
          <label>parent_task_id <input name="parent_task_id" type="number" placeholder="optional"/></label>
        </div>
        <div class="form-row"><label>payload JSON
          <textarea name="payload_json" rows="3" style="width:95%;font-family:monospace">{{}}</textarea>
        </label></div>
        <button type="submit">Create task</button>
      </form>
    </div>
    """

    js = """
<script>
async function readErr(r){
  try{ const j=await r.json(); return j.error||JSON.stringify(j);}catch(e){return r.statusText;}
}
async function postVersion(ev){
  ev.preventDefault();
  const f=ev.target;
  const body={
    channel_id: +f.channel_id.value,
    module_id: +f.module_id.value,
    version_label: f.version_label.value.trim(),
    title: f.title.value||null,
    notes: f.notes.value||null,
    status: f.status.value
  };
  const r=await fetch('/api/versions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  const j=await r.json();
  location.href='/tasks?flash='+encodeURIComponent('version #'+j.id+' created');
  return false;
}
async function postBundle(ev){
  ev.preventDefault();
  const f=ev.target;
  let fields;
  try{ fields=JSON.parse(f.fields_json.value);}catch(e){alert('fields JSON invalid');return false;}
  const body={
    channel_id: +f.channel_id.value,
    module_id: +f.module_id.value,
  if(f.writer && f.writer.value.trim()) body.writer=f.writer.value.trim();
  if(f.session_id && f.session_id.value.trim()) body.session_id=f.session_id.value.trim();
  const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  const j=await r.json();
  location.href='/tasks?task_id='+j.id+'&flash='+encodeURIComponent('task '+j.task_label+' created');
  return false;
}
async function postTaskContext(ev){
  ev.preventDefault();
  const f=ev.target;
  const tid= +f.task_id.value;
  const body={ writer: f.writer.value, session_id: f.session_id.value };
  const r=await fetch('/api/tasks/'+tid+'/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent('context sav
  };
  if(f.parent_task_id && f.parent_task_id.value) body.parent_task_id= +f.parent_task_id.value;
  const r=await fetch('/api/tasks/bundle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  const j=await r.json();
  location.href='/tasks?task_id='+(j.root_id||j.id)+'&flash='+encodeURIComponent('bundle created');
  return false;
}
async function postTask(ev){
  ev.preventDefault();
  const f=ev.target;
  let payload=f.payload_json.value.trim()||null;
  if(payload){ try{ JSON.parse(payload);}catch(e){alert('payload JSON invalid');return false;} }
  const body={
    channel_id: +f.channel_id.value,
    module_id: +f.module_id.value,
    version_id: +f.version_id.value,
    action_name_id: +f.action_name_id.value,
    task_label: f.task_label.value.trim(),
    title: f.title.value.trim(),
    payload_json: payload
  };
  if(f.parent_task_id.value) body.parent_task_id= +f.parent_task_id.value;
  const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  const j=await r.json();
  location.href='/tasks?task_id='+j.id+'&flash='+encodeURIComponent('task '+j.task_label+' created');
  return false;
}
async function postAddField(ev){
  ev.preventDefault();
  const f=ev.target;
  const tid= +f.task_id.value;
  const body={
    field_name: f.field_name.value.trim(),
    tdd_type_id: +f.tdd_type_id.value,
    nullable: +f.nullable.value,
    is_pk: +f.is_pk.value,
    sort_order: +f.sort_order.value
  };
  const r=await fetch('/api/tasks/'+tid+'/fields',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent('field added');
  return false;
}
async function runAssemble(tid){
  const r=await fetch('/api/tasks/'+tid+'/assemble',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const c=j.counts||{};
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent(
    'STEP4 assemble add='+(c.add||0)+' update='+(c.update||0)+' del='+(c.del||0)
  );
}
async function runVision(tid, noOllama){
  const body = noOllama ? {no_ollama:true} : {};
  const r=await fetch('/api/tasks/'+tid+'/vision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const msg = 'STEP2 vision rows='+(j.row_count||0)+' err='+(j.step2_error||'-');
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent(msg);
}
async function runReport(tid, doNotify){
  const body = doNotify ? {notify:true} : {};
  const r=await fetch('/api/tasks/'+tid+'/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const msg = 'STEP5 report #'+(j.report_id||'-')+' gate=never';
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent(msg);
}
async function runSupervise(tid, withOllama){
  const body = withOllama ? {ollama:true} : {};
  const r=await fetch('/api/tasks/'+tid+'/supervise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const msg = 'Phase5 '+(j.summary||('ok='+j.ok));
  location.href='/tasks?task_id='+(j.remediate_task_id||tid)+'&flash='+encodeURIComponent(msg);
}
async function runQc(tid, withMcp){
  const body = {detached:true, write_ssot:true, mcp:!!withMcp};
  const r=await fetch('/api/tasks/'+tid+'/run-qc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const msg = 'QC '+j.mode+' table='+(j.table||'-')+' task='+(j.task_label||tid);
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent(msg);
}
async function runMatch(tid){
  const r=await fetch('/api/tasks/'+tid+'/match',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  const j=await r.json();
  const msg = 'FH6 match status='+(j.match_status||'-')+' score='+(j.match_score||'-')+' option='+(j.option_code||'-');
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent(msg);
}
async function postTaskSsot(ev){
  ev.preventDefault();
  const f=ev.target;
  const tid= +f.task_id.value;
  const body={
    dim_key: f.dim_key.value.trim(),
    value_text: f.value_text.value,
    value_type: f.value_type.value,
    source: f.source.value.trim()||'manual',
    sort_order: +f.sort_order.value||0,
    notes: f.notes.value||null
  };
  const r=await fetch('/api/tasks/'+tid+'/ssot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){alert('FAIL: '+await readErr(r));return false;}
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent('task_ssot upserted');
  return false;
}
async function delTaskSsot(tid, sid){
  if(!confirm('Delete task_ssot #'+sid+'?')) return;
  const r=await fetch('/api/tasks/'+tid+'/ssot/'+sid,{method:'DELETE'});
  if(!r.ok){alert('FAIL: '+await readErr(r));return;}
  location.href='/tasks?task_id='+tid+'&flash='+encodeURIComponent('task_ssot deleted');
}
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>Task Center</title>
<style>{SHARED_CSS}</style></head><body>
{nav_html("tasks")}
<h1>Task Center</h1>
<p class="meta">Local only · hierarchy: channel → module → version → task (parent_task_id)
 · action is task attribute · create does not apply DDL</p>
{err_html}{flash_html}
<form method="get" action="/tasks">
  <label>channel <select name="channel_id">{ch_opts}</select></label>
  <label>module <select name="module_id">{mo_opts}</select></label>
  <label>version <select name="version_id">{ver_opts}</select></label>
  <label>status <select name="status">{''.join(st_opts)}</select></label>
  <button type="submit">Filter</button>
  <a class="meta" href="/tasks">reset</a>
</form>
<p class="meta">API: <code>/api/tasks</code> · <code>/api/tasks/<id></code> ·
<code>/api/versions</code> · POST bundle <code>/api/tasks/bundle</code>
· <a href="/health">Code Health</a> (<code>/api/health</code>)</p>
<h2>Tree · {tree_data.get('count', 0)} tasks</h2>
<div class="wrap"><table>
<thead><tr><th>label</th><th>title</th><th>action</th><th>status</th><th>ch/mod/ver</th><th></th></tr></thead>
<tbody>{tree_rows}</tbody>
</table></div>
{detail_html}
{create_panel}
{js}
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html_text: str):
        raw = (html_text or "").encode("utf-8")
        self._send(code, raw, "text/html; charset=utf-8")

    def _json(self, code: int, obj):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _parse_int(self, qs, key):
        v = (qs.get(key) or [None])[0]
        if v is None or v == "":
            return None
        return int(v)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        try:
            conn = connect()
        except Exception as e:
            self._json(500, {"error": str(e)})
            return

        try:
            if path == "/api/tables":
                self._json(200, {"tables": list_tables(conn), "db": str(DB_PATH)})
                return

            if path.startswith("/api/table/"):
                name = path[len("/api/table/") :].strip("/")
                if not allowed_table(conn, name):
                    self._json(404, {"error": "unknown table", "table": name})
                    return
                try:
                    limit = min(
                        MAX_LIMIT, max(1, int(qs.get("limit", [DEFAULT_LIMIT])[0]))
                    )
                except ValueError:
                    limit = DEFAULT_LIMIT
                self._json(200, fetch_table(conn, name, limit))
                return

            if path.startswith("/api/schema/"):
                name = path[len("/api/schema/") :].strip("/")
                if not allowed_table(conn, name):
                    self._json(
                        404,
                        {"error": "unknown table", "table": name, "exists": False},
                    )
                    return
                self._json(200, fetch_schema(conn, name))
                return

            if path == "/api/channels":
                self._json(200, {"channels": fetch_dims(conn)["channels"]})
                return
            if path == "/api/modules":
                self._json(200, {"modules": fetch_dims(conn)["modules"]})
                return
            if path == "/api/actions":
                self._json(200, {"actions": fetch_dims(conn)["actions"]})
                return
            if path == "/api/tdd-types":
                self._json(200, {"tdd_types": fetch_dims(conn)["tdd_types"]})
        
            if path == "/api/tasks":
                filters = {
                    "channel_id": self._parse_int(qs, "channel_id"),
                    "module_id": self._parse_int(qs, "module_id"),
                    "version_id": self._parse_int(qs, "version_id"),
                    "status": (qs.get("status") or [None])[0] or None,
                }
                data = fetch_task_tree(
                    conn,
                    channel_id=filters["channel_id"],
                    module_id=filters["module_id"],
                    version_id=filters["version_id"],
                    status=filters["status"],
                )
                self._json(200, data)
                return

            if path.startswith("/api/tasks/"):
                rest = path[len("/api/tasks/") :].strip("/")
                if rest.isdigit():
                    detail = fetch_task_detail(conn, int(rest))
                    if not detail:
                        self._json(404, {"error": "task not found", "id": int(rest)})
                        return
                    self._json(200, detail)
                    return
                self._json(404, {"error": "not found", "path": path})
                return

            if path in ("/api/health", "/api/health/"):
                if generate_code_health_report is None:
                    self._json(500, {"error": "code_health unavailable", "gate": "never"})
                    return
                try:
                    report = generate_code_health_report(conn)
                except Exception as e:
                    self._json(500, {"error": str(e), "gate": "never"})
                    return
                self._json(200, report)
                return

            if path.startswith("/api/health/branch"):
                if get_tacid_branch_function_report is None:
                    self._json(500, {"error": "branch report unavailable", "gate": "never"})
                    return
                tacid = (qs.get("tacid") or [None])[0]
                try:
                    report = get_tacid_branch_function_report(conn, tacid=tacid)
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, report)
                return



            # HTML pages
            if path in ("/", "/index", "/index.html"):
                tables = list_tables(conn)
                selected = (qs.get("table") or [None])[0]
                err = None
                data = None
                schema = None
                try:
                    limit = min(
                        MAX_LIMIT, max(1, int((qs.get("limit") or [DEFAULT_LIMIT])[0]))
                    )
                except ValueError:
                    limit = DEFAULT_LIMIT
                if selected:
                    if not allowed_table(conn, selected):
                        err = f"unknown table: {selected}"
                        selected = None
                    else:
                        try:
                            data = fetch_table(conn, selected, limit)
                            schema = fetch_schema(conn, selected)
                        except Exception as e:
                            err = str(e)
                self._html(200, page_html(tables, selected, data, err, schema=schema))
                return

            if path in ("/tasks", "/tasks/"):
                filters = {
                    "channel_id": self._parse_int(qs, "channel_id"),
                    "module_id": self._parse_int(qs, "module_id"),
                    "version_id": self._parse_int(qs, "version_id"),
                    "status": (qs.get("status") or [None])[0] or None,
                    "task_id": self._parse_int(qs, "task_id"),
                }
                flash = (qs.get("flash") or [None])[0]
                err = (qs.get("err") or [None])[0]
                dims = fetch_dims(conn)
                tree_data = fetch_task_tree(
                    conn,
                    channel_id=filters["channel_id"],
                    module_id=filters["module_id"],
                    version_id=filters["version_id"],
                    status=filters["status"],
                )
                detail = None
                if filters.get("task_id") is not None:
                    detail = fetch_task_detail(conn, int(filters["task_id"]))
                html_out = tasks_page_html(
                    dims,
                    tree_data,
                    detail,
                    filters,
                    err=err,
                    flash=flash,
                )
                self._html(200, html_out)
                return

            if path in ("/health", "/health/"):
                flash = (qs.get("flash") or [None])[0]
                err = (qs.get("err") or [None])[0]
                prefix = (qs.get("prefix") or ["ch.demo"])[0] or "ch.demo"
                report = None
                branch = None
                schema_ok = None
                try:
                    if verify_code_health_schema is not None:
                        schema_ok = bool(verify_code_health_schema(conn))
                    if generate_code_health_report is not None:
                        report = generate_code_health_report(conn)
                    tacid = (qs.get("tacid") or [None])[0]
                    if tacid and get_tacid_branch_function_report is not None:
                        branch = get_tacid_branch_function_report(conn, tacid=tacid)
                except Exception as e:
                    err = str(e)
                self._html(
                    200,
                    health_page_html(
                        report,
                        branch,
                        err=err,
                        flash=flash,
                        prefix=prefix,
                        schema_ok=schema_ok,
                    ),
                )
                return

            if path in ("/managed", "/managed/"):
                flash = (qs.get("flash") or [None])[0]
                err = (qs.get("err") or [None])[0]
                system_key = (qs.get("system_key") or ["membership"])[0] or "membership"
                report = None
                systems = None
                schema_ok = None
                builder = None
                try:
                    if worker_clean_report is not None:
                        report = worker_clean_report(conn)
                    if list_managed_systems is not None:
                        systems = list_managed_systems(conn)
                    if verify_managed_schema is not None:
                        schema_ok = bool(verify_managed_schema(conn))
                except Exception as e:
                    err = str(e)
                self._html(
                    200,
                    managed_page_html(
                        report,
                        systems,
                        err=err,
                        flash=flash,
                        system_key=system_key,
                        schema_ok=schema_ok,
                        builder=builder,
                        selected_register_id=(qs.get("register_id") or [None])[0],
                        selected_slice=(qs.get("slice") or [None])[0],
                    ),
                )
                return

            if path in ("/pair", "/pair/"):
                flash = (qs.get("flash") or [None])[0]
                err = (qs.get("err") or [None])[0]
                dash = None
                schema_ok = None
                try:
                    if pair_qc_dashboard is not None:
                        dash = pair_qc_dashboard(conn)
                    if verify_pair_qc_schema is not None:
                        schema_ok = bool(verify_pair_qc_schema(conn))
                except Exception as e:
                    err = str(e)
                self._html(
                    200,
                    pair_page_html(dash, err=err, flash=flash, schema_ok=schema_ok),
                )
                return

            if path in ("/hko", "/hko/"):
                flash = (qs.get("flash") or [None])[0]
                err = (qs.get("err") or [None])[0]
                dash = None
                try:
                    if hko_dashboard is not None:
                        dash = hko_dashboard(conn)
                except Exception as e:
                    err = str(e)
                self._html(200, hko_page_html(dash, err=err, flash=flash))
                return

            self._json(404, {"error": "not found", "path": path})
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()})
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            body = self._read_json()
        except Exception as e:
            self._json(400, {"error": f"invalid json: {e}"})
            return

        try:
            conn = connect()
        except Exception as e:
            self._json(500, {"error": str(e)})
            return

        try:
            if path == "/api/versions":
                try:
                    result = create_version(
                        conn,
                        channel_id=int(body["channel_id"]),
                        module_id=int(body["module_id"]),
                        version_label=str(body["version_label"]),
                        title=body.get("title"),
                        notes=body.get("notes"),
                        status=str(body.get("status") or "draft"),
                        parent_version_id=(
                            int(body["parent_version_id"])
                            if body.get("parent_version_id") not in (None, "")
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(201, result)
                return

            if path == "/api/tasks/bundle":
                try:
                    result = create_task_bundle(
                        conn,
                        channel_id=int(body["channel_id"]),
                        module_id=int(body["module_id"]),
                        version_id=int(body["version_id"]),
                        root_label=str(body["root_label"]),
                        title=str(body["title"]),
                        table_name=str(body["table_name"]),
                        fields=list(body.get("fields") or []),
                        browser_base=f"http://{HOST}:{PORT}",
                        status=str(body.get("status") or "pending"),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(201, result)
                return


            if path == "/api/tasks":
                try:
                    payload = body.get("payload_json")
                    if isinstance(payload, dict):
                        pass
                    elif isinstance(payload, str) and payload.strip():
                        payload = payload  # create_dev_task accepts str
                    else:
                        payload = body.get("payload")  # dict ok
                    result = create_dev_task(
                        conn,
                        channel_id=int(body["channel_id"]),
                        module_id=int(body["module_id"]),
                        version_id=int(body["version_id"]),
                        action_name_id=int(body["action_name_id"]),
                        task_label=str(body["task_label"]),
                        title=str(body["title"]),
                        parent_task_id=(
                            int(body["parent_task_id"])
                            if body.get("parent_task_id") not in (None, "")
                            else None
                        ),
                        payload_json=payload,
                        status=str(body.get("status") or "pending"),
                        writer=(
                            str(body.get("writer"))
                            if body.get("writer") not in (None, "")
                            else None
                        ),
                        session_id=(
                            str(body.get("session_id"))
                            if body.get("session_id") not in (None, "")
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(201, result)
                return

            if path.startswith("/api/tasks/") and path.endswith("/context"):
                mid = path[len("/api/tasks/") : -len("/context")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if update_dev_task_context is None:
                    self._json(500, {"error": "update_dev_task_context unavailable"})
                    return
                try:
                    set_writer = "writer" in body
                    set_session = "session_id" in body
                    result = update_dev_task_context(
                        conn,
                        int(mid),
                        writer=body.get("writer"),
                        session_id=body.get("session_id"),
                        set_writer=set_writer,
                        set_session_id=set_session,
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            if path.startswith("/api/tasks/") and path.endswith("/fields"):
                mid = path[len("/api/tasks/") : -len("/fields")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                try:
                    result = add_dev_task_field(
                        conn,
                        task_id=int(mid),
                        field_name=str(body["field_name"]),
                        tdd_type_id=(
                            int(body["tdd_type_id"])
                            if body.get("tdd_type_id") is not None
                            else None
                        ),
                        tdd_code=body.get("tdd_code"),
                        nullable=int(body.get("nullable", 1)),
                        is_pk=int(body.get("is_pk", 0)),
                        default_text=body.get("default_text"),
                        sort_order=int(body.get("sort_order", 0)),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(201, result)
                return

            # FH2: POST /api/tasks/{id}/ssot
            if path.startswith("/api/tasks/") and path.endswith("/ssot"):
                mid = path[len("/api/tasks/") : -len("/ssot")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if upsert_task_ssot is None:
                    self._json(500, {"error": "upsert_task_ssot unavailable"})
                    return
                try:
                    result = upsert_task_ssot(
                        conn,
                        task_id=int(mid),
                        dim_key=str(body["dim_key"]),
                        value_text=(
                            None
                            if body.get("value_text") is None
                            else str(body.get("value_text"))
                        ),
                        value_type=str(body.get("value_type") or "string"),
                        source=str(body.get("source") or "manual"),
                        parent_dim_id=(
                            int(body["parent_dim_id"])
                            if body.get("parent_dim_id") not in (None, "")
                            else None
                        ),
                        sort_order=int(body.get("sort_order") or 0),
                        notes=body.get("notes"),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(201, result)
                return

            # FH3: POST /api/tasks/{id}/assemble
            if path.startswith("/api/tasks/") and path.endswith("/assemble"):
                mid = path[len("/api/tasks/") : -len("/assemble")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_fail_ssot_assemble is None:
                    self._json(500, {"error": "run_fail_ssot_assemble unavailable"})
                    return
                try:
                    result = run_fail_ssot_assemble(
                        conn,
                        remediate_task_id=int(mid),
                        case_id=(
                            int(body["case_id"])
                            if body.get("case_id") not in (None, "")
                            else None
                        ),
                        write_payload=bool(body.get("write_payload", True)),
                        write_facts=bool(body.get("write_facts", True)),
                        commit=True,
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # FH4: POST /api/tasks/{id}/vision  (STEP2 Ollama QC PNG — not a gate)
            if path.startswith("/api/tasks/") and path.endswith("/vision"):
                mid = path[len("/api/tasks/") : -len("/vision")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_fail_vision_step2 is None:
                    self._json(500, {"error": "run_fail_vision_step2 unavailable"})
                    return
                try:
                    no_ollama = bool(
                        body.get("no_ollama")
                        or body.get("skip_ollama")
                        or body.get("resolve_only")
                    )
                    result = run_fail_vision_step2(
                        conn,
                        remediate_task_id=int(mid),
                        case_id=(
                            int(body["case_id"])
                            if body.get("case_id") not in (None, "")
                            else None
                        ),
                        vision_id=(
                            int(body["vision_id"])
                            if body.get("vision_id") not in (None, "")
                            else None
                        ),
                        image_path=(
                            str(body["image_path"])
                            if body.get("image_path") not in (None, "")
                            else None
                        ),
                        write_payload=bool(body.get("write_payload", True)),
                        write_facts=bool(body.get("write_facts", True)),
                        commit=True,
                        call_ollama=not no_ollama,
                        model=body.get("model"),
                        timeout=(
                            float(body["timeout"])
                            if body.get("timeout") not in (None, "")
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # FH5: POST /api/tasks/{id}/report  (STEP5 rollup — not a gate)
            if path.startswith("/api/tasks/") and path.endswith("/report"):
                mid = path[len("/api/tasks/") : -len("/report")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_fail_report is None:
                    self._json(500, {"error": "run_fail_report unavailable"})
                    return
                try:
                    result = run_fail_report(
                        conn,
                        remediate_task_id=int(mid),
                        case_id=(
                            int(body["case_id"])
                            if body.get("case_id") not in (None, "")
                            else None
                        ),
                        write_payload=bool(body.get("write_payload", True)),
                        write_facts=bool(body.get("write_facts", True)),
                        write_db=bool(body.get("write_db", True)),
                        notify=bool(body.get("notify", False)),
                        commit=True,
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # FH6: POST /api/tasks/{id}/match  (option SSOT match — not a gate)
            if path.startswith("/api/tasks/") and path.endswith("/match"):
                mid = path[len("/api/tasks/") : -len("/match")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_fault_ssot_match is None:
                    self._json(500, {"error": "run_fault_ssot_match unavailable"})
                    return
                try:
                    try:
                        from db_schema import seed_ssot_defaults

                        seed_ssot_defaults(conn)
                        conn.commit()
                    except Exception:
                        pass
                    result = run_fault_ssot_match(
                        conn,
                        remediate_task_id=int(mid),
                        case_id=(
                            int(body["case_id"])
                            if body.get("case_id") not in (None, "")
                            else None
                        ),
                        write_event=bool(body.get("write_event", True)),
                        write_analysis=bool(body.get("write_analysis", True)),
                        write_facts=bool(body.get("write_facts", True)),
                        write_payload=bool(body.get("write_payload", True)),
                        commit=True,
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # Phase5: POST /api/tasks/{id}/supervise  (B orchestrator — not a gate)
            if path.startswith("/api/tasks/") and path.endswith("/supervise"):
                mid = path[len("/api/tasks/") : -len("/supervise")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_fail_handling_supervisor is None:
                    self._json(500, {"error": "run_fail_handling_supervisor unavailable"})
                    return
                try:
                    steps = body.get("steps")
                    if isinstance(steps, str):
                        steps = [s.strip() for s in steps.split(",") if s.strip()]
                    detached = bool(body.get("detached", False))
                    call_ollama = bool(
                        body.get("ollama")
                        or body.get("call_ollama")
                        or body.get("with_ollama")
                    )
                    if detached and spawn_detached_fail_handling is not None:
                        import sys

                        argv = [
                            sys.executable,
                            str(Path(__file__).resolve().parent / "fail_handling.py"),
                            "supervise",
                            "--task-id",
                            str(int(mid)),
                            "--db",
                            str(DB_PATH),
                        ]
                        if body.get("case_id") not in (None, ""):
                            argv.extend(["--case-id", str(int(body["case_id"]))])
                        if call_ollama:
                            argv.append("--ollama")
                        if body.get("notify"):
                            argv.append("--notify")
                        if steps:
                            argv.extend(["--steps", ",".join(steps)])
                        log_path = spawn_detached_fail_handling(
                            argv, log_name=f"fh_ui_{mid}.log"
                        )
                        self._json(
                            200,
                            {
                                "ok": True,
                                "mode": "detached",
                                "task_id": int(mid),
                                "log": log_path,
                                "gate": "never",
                            },
                        )
                        return
                    result = run_fail_handling_supervisor(
                        conn,
                        task_id=int(mid),
                        case_id=(
                            int(body["case_id"])
                            if body.get("case_id") not in (None, "")
                            else None
                        ),
                        steps=steps,
                        call_ollama=call_ollama,
                        notify=bool(body.get("notify", False)),
                        write=bool(body.get("write", True)),
                        commit=True,
                        vision_id=(
                            int(body["vision_id"])
                            if body.get("vision_id") not in (None, "")
                            else None
                        ),
                        image_path=(
                            str(body["image_path"])
                            if body.get("image_path") not in (None, "")
                            else None
                        ),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # Phase5: POST /api/tasks/{id}/run-qc  (A hard gate runner — detached default)
            if path.startswith("/api/tasks/") and path.endswith("/run-qc"):
                mid = path[len("/api/tasks/") : -len("/run-qc")].strip("/")
                if not mid.isdigit():
                    self._json(404, {"error": "not found", "path": path})
                    return
                if run_qc_for_task is None:
                    self._json(500, {"error": "run_qc_for_task unavailable"})
                    return
                try:
                    result = run_qc_for_task(
                        conn,
                        int(mid),
                        db_path=DB_PATH,
                        with_mcp=bool(
                            body.get("mcp")
                            or body.get("with_mcp")
                            or body.get("mcp_shot")
                        ),
                        write_ssot=bool(body.get("write_ssot", True)),
                        detached=bool(body.get("detached", True)),
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # Phase5 bulk: POST /api/fail-handling/spawn-pending
            if path == "/api/fail-handling/spawn-pending":
                if spawn_pending_fail_handling is None:
                    self._json(500, {"error": "spawn_pending_fail_handling unavailable"})
                    return
                try:
                    steps = body.get("steps")
                    if isinstance(steps, str):
                        steps = [s.strip() for s in steps.split(",") if s.strip()]
                    result = spawn_pending_fail_handling(
                        db_path=str(DB_PATH),
                        call_ollama=bool(body.get("ollama") or body.get("call_ollama")),
                        notify=bool(body.get("notify", False)),
                        steps=steps,
                    )
                except (KeyError, TypeError, ValueError) as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, result)
                return

            # CH6: POST /api/health/harvest  (pipeline C — never a gate)
            if path in ("/api/health/harvest", "/api/health/harvest/"):
                if harvest_static_impl_refs is None:
                    self._json(500, {"error": "code_health harvest unavailable", "gate": "never"})
                    return
                try:
                    result = harvest_static_impl_refs(conn, commit=True)
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # CH7/W12: POST /api/health/cleanup — spawn code.cleanup tasks
            if path in ("/api/health/cleanup", "/api/health/cleanup/"):
                if spawn_dead_function_cleanup_tasks is None:
                    self._json(
                        500,
                        {"error": "code_health cleanup unavailable", "gate": "never"},
                    )
                    return
                try:
                    limit = int(body.get("limit") or 50)
                    result = spawn_dead_function_cleanup_tasks(
                        conn, limit=limit, commit=True, source="ui.health"
                    )
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # CH7/W11: POST /api/health/bind — file + line (+ optional code span)
            if path in ("/api/health/bind", "/api/health/bind/"):
                if bind_register_source_location is None:
                    self._json(
                        500,
                        {"error": "bind_register_source_location unavailable", "gate": "never"},
                    )
                    return
                try:
                    result = bind_register_source_location(
                        conn,
                        register_id=(
                            str(body.get("register_id") or body.get("register") or "").strip()
                            or None
                        ),
                        module_name=(
                            str(body.get("module") or body.get("module_name") or "").strip()
                            or None
                        ),
                        function_name=(
                            str(
                                body.get("function")
                                or body.get("function_name")
                                or body.get("fn")
                                or ""
                            ).strip()
                            or None
                        ),
                        file_path=str(body.get("file") or body.get("file_path") or "").strip(),
                        line_start=int(body.get("line_start") or body.get("line") or 1),
                        line_end=(
                            int(body["line_end"])
                            if body.get("line_end") not in (None, "")
                            else None
                        ),
                        code_span=(
                            str(body.get("code") or body.get("code_span") or "") or None
                        ),
                        commit=True,
                    )
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # MCS8: POST /api/managed/build  (human request → research → build)
            if path in ("/api/managed/build", "/api/managed/build/"):
                if run_function_builder is None:
                    self._json(
                        500,
                        {"error": "function builder unavailable", "gate": "never"},
                    )
                    return
                try:
                    text = str(
                        body.get("text")
                        or body.get("human_text")
                        or body.get("request")
                        or ""
                    ).strip()
                    module = str(
                        body.get("module") or body.get("module_code") or ""
                    ).strip()
                    if not text or not module:
                        self._json(
                            400,
                            {
                                "error": "text and module required",
                                "gate": "never",
                            },
                        )
                        return
                    result = run_function_builder(
                        conn,
                        human_text=text,
                        module_code=module,
                        channel_code=str(
                            body.get("channel") or body.get("channel_code") or "local_pc"
                        ),
                        system_key=(
                            str(body.get("system") or body.get("system_key") or "").strip()
                            or None
                        ),
                        desired_table=(
                            str(body.get("table") or body.get("desired_table") or "").strip()
                            or None
                        ),
                        desired_output=(
                            str(body.get("output") or body.get("desired_output") or "").strip()
                            or None
                        ),
                        fields=body.get("fields"),
                        request_kind=str(body.get("kind") or body.get("request_kind") or "system"),
                        commit=True,
                    )
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # MCS5: POST /api/managed/seed  (membership Task 1 — never a gate)
            if path in ("/api/managed/seed", "/api/managed/seed/"):
                if seed_membership_system is None:
                    self._json(
                        500,
                        {"error": "managed_coding seed unavailable", "gate": "never"},
                    )
                    return
                try:
                    result = seed_membership_system(conn, commit=True)
                    if mark_demo_noise_rubbish is not None:
                        result["cleanup"] = mark_demo_noise_rubbish(conn, commit=True)
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # MCS5: POST /api/managed/cleanup  (mark rubbish only — never delete source)
            if path in ("/api/managed/cleanup", "/api/managed/cleanup/"):
                if mark_demo_noise_rubbish is None:
                    self._json(
                        500,
                        {"error": "managed_coding cleanup unavailable", "gate": "never"},
                    )
                    return
                try:
                    result = mark_demo_noise_rubbish(conn, commit=True)
                except Exception as e:
                    self._json(400, {"error": str(e), "gate": "never"})
                    return
                self._json(200, result)
                return

            # HKO dual-path proof run (gate=never; can be slow)
            if path in ("/api/hko/run", "/api/hko/run/"):
                if hko_run_proof is None:
                    self._json(
                        500,
                        {"error": "hko_weather_proof unavailable", "gate": "never"},
                    )
                    return
                try:
                    headed = bool(body.get("headed") or False)
                    delay = float(
                        body.get("delay") if body.get("delay") is not None else 4.0
                    )
                    skip_openclaw = bool(body.get("skip_openclaw") or False)
                    skip_playwright = bool(body.get("skip_playwright") or False)
                    skip_ollama = bool(body.get("skip_ollama") or False)
                    url = body.get("url")
                    kwargs = {
                        "headed": headed,
                        "openclaw_delay": delay,
                        "skip_openclaw": skip_openclaw,
                        "skip_playwright": skip_playwright,
                        "skip_ollama": skip_ollama,
                    }
                    if url:
                        kwargs["url"] = str(url)
                    result = hko_run_proof(**kwargs)
                except Exception as e:
                    self._json(
                        500,
                        {
                            "error": f"{type(e).__name__}: {e}",
                            "gate": "never",
                            "trace": traceback.format_exc()[-2000:],
                        },
                    )
                    return
                self._json(
                    200,
                    {
                        "gate": "never",
                        "pipeline": "HKO_dual_path_proof",
                        "run": result,
                    },
                )
                return

            self._json(404, {"error": "not found", "path": path})
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()})
        finally:
            conn.close()



    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            conn = connect()
        except Exception as e:
            self._json(500, {"error": str(e)})
            return

        try:
            # FH2: DELETE /api/tasks/{tid}/ssot/{sid}
            if path.startswith("/api/tasks/") and "/ssot/" in path:
                rest = path[len("/api/tasks/") :].strip("/")
                parts = rest.split("/")
                if (
                    len(parts) == 3
                    and parts[0].isdigit()
                    and parts[1] == "ssot"
                    and parts[2].isdigit()
                ):
                    if delete_task_ssot is None:
                        self._json(500, {"error": "delete_task_ssot unavailable"})
                        return
                    try:
                        result = delete_task_ssot(
                            conn,
                            task_id=int(parts[0]),
                            ssot_id=int(parts[2]),
                        )
                    except (TypeError, ValueError) as e:
                        self._json(400, {"error": str(e)})
                        return
                    self._json(200, result)
                    return

            self._json(404, {"error": "not found", "path": path})
        except Exception as e:
            self._json(500, {"error": str(e), "trace": traceback.format_exc()})
        finally:
            conn.close()


def _resolve_port(args_port: int | None) -> int:
    """Return effective port: CLI > settings table > default."""
    if args_port is not None and args_port > 0:
        return args_port
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            port = get_setting(conn, "browser.port", PORT)
            return int(port)
        finally:
            conn.close()
    except Exception:
        return PORT


def main():
    global PORT, HOST
    ap = argparse.ArgumentParser(description="Local agent.db browser + Task Center")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=HOST)
    ap.add_argument(
        "--detach",
        action="store_true",
        help="Run as a detached background process (survives IDE/terminal close)",
    )
    args = ap.parse_args()

    if args.detach:
        argv = [sys.executable, str(Path(__file__).resolve())]
        if args.port is not None:
            argv.extend(["--port", str(args.port)])
        if args.host != HOST:
            argv.extend(["--host", args.host])
        subprocess.Popen(
            argv,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        print(f"db_browser detached on http://{args.host}:{args.port or PORT}")
        return

    PORT = _resolve_port(args.port)
    HOST = args.host

    if ensure_schema is not None:
        try:
            ensure_schema(str(DB_PATH))
        except Exception as e:
            print(f"schema ensure skipped: {e}")

    if not DB_PATH.is_file():
        print(f"ERROR: DB not found: {DB_PATH}")
        sys.exit(1)

    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("Refusing non-local bind. Use 127.0.0.1")
        sys.exit(2)

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"DB: {DB_PATH}")
    print(f"Open {url}")
    print(f"Tasks {url}tasks")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        httpd.server_close()


if __name__ == "__main__":
    main()
