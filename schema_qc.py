"""Schema QC runner — prove table/fields exist (PRAGMA/API authority).

Usage:
  python schema_qc.py --table vision_asset
  python schema_qc.py --table vision_asset --expected id,kind,path_or_url,sha256,source,created_at
  python schema_qc.py --table vision_asset --task-label 1.1b
  python schema_qc.py --table vision_asset --write-ssot
  python schema_qc.py --table vision_asset --task-label 1.1b --write-ssot --mcp-shot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "agent.db"
QC_EVIDENCE_DIR = BASE_DIR / "qc_evidence"
DEFAULT_BROWSER_HOST = "127.0.0.1"
DEFAULT_BROWSER_PORT = 8766
DEFAULT_OPEN_DELAY = 2.5

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from db_schema import ensure_schema, get_setting, record_schema_qc_hard_fail
except Exception:
    ensure_schema = None
    record_schema_qc_hard_fail = None
    get_setting = None


def _browser_port() -> int:
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            return int(get_setting(conn, "browser.port", DEFAULT_BROWSER_PORT))
        finally:
            conn.close()
    except Exception:
        return DEFAULT_BROWSER_PORT


def default_browser_url(table: str) -> str:
    return f"http://{DEFAULT_BROWSER_HOST}:{_browser_port()}/?table={table}&limit=200"


def api_schema_url(table: str) -> str:
    return f"http://{DEFAULT_BROWSER_HOST}:{_browser_port()}/api/schema/{table}"


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def observe_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    if not exists:
        return {
            "table": table,
            "exists": False,
            "column_count": 0,
            "column_names": [],
            "columns": [],
            "row_count": 0,
        }
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2] or "",
            "notnull": bool(r[3]),
            "dflt_value": r[4],
            "pk": int(r[5] or 0),
        }
        for r in info
    ]
    names = [c["name"] for c in columns]
    row_count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return {
        "table": table,
        "exists": True,
        "column_count": len(columns),
        "column_names": names,
        "columns": columns,
        "row_count": row_count,
        "has_updated_at": "updated_at" in names,
        "has_code": "code" in names,
        "has_name_col": "name" in names,
    }


def diff_expected(observed: dict, expected_columns: list[str] | None) -> dict[str, Any]:
    """HARD GATE only: pass/fail from PRAGMA vs expected exact set.

    - Table missing → fail
    - No expected list → pass if table exists (existence-only gate)
    - Expected provided → pass iff set(observed) == set(expected)
      (missing OR extra → fail). Column order is NOT a gate.
    """
    if not observed.get("exists"):
        return {
            "match_ok": False,
            "missing_columns": list(expected_columns or []),
            "extra_columns": [],
            "order_prefix_ok": False,
            "summary": f"FAIL table {observed.get('table')} does not exist",
        }
    if not expected_columns:
        return {
            "match_ok": True,
            "missing_columns": [],
            "extra_columns": [],
            "order_prefix_ok": True,
            "summary": (
                f"PASS exists columns={observed['column_count']} "
                f"names={observed['column_names']}"
            ),
        }
    obs = list(observed["column_names"])
    exp = list(expected_columns)
    missing = [c for c in exp if c not in obs]
    extra = [c for c in obs if c not in exp]
    order_ok = obs == exp
    # HARD GATE: exact set (order ignored for match_ok)
    match_ok = not missing and not extra
    summary_parts = []
    if match_ok:
        summary_parts.append("PASS")
    else:
        summary_parts.append("FAIL")
    summary_parts.append(f"table={observed['table']}")
    summary_parts.append(f"observed={obs}")
    summary_parts.append(f"expected={exp}")
    if missing:
        summary_parts.append(f"missing={missing}")
    if extra:
        summary_parts.append(f"extra={extra}")
    if match_ok and not order_ok:
        summary_parts.append("note=order_differs_not_gate")
    return {
        "match_ok": match_ok,
        "missing_columns": missing,
        "extra_columns": extra,
        "order_prefix_ok": order_ok,
        "summary": " | ".join(summary_parts),
    }


def upsert_schema_ssot(
    conn: sqlite3.Connection,
    table: str,
    observed: dict,
    diff: dict,
    task_id: int | None,
    version_id: int | None,
    browser_url: str,
    *,
    vision_id: int | None = None,
    vision_path: str | None = None,
) -> int:
    now = _now_iso()
    pairs = [
        ("exists", str(bool(observed.get("exists"))).lower(), "bool", "pragma"),
        ("column_count", str(observed.get("column_count", 0)), "number", "pragma"),
        (
            "columns_json",
            json.dumps(observed.get("column_names", []), ensure_ascii=False),
            "json",
            "pragma",
        ),
        ("has_updated_at", str(bool(observed.get("has_updated_at"))).lower(), "bool", "pragma"),
        ("has_code", str(bool(observed.get("has_code"))).lower(), "bool", "pragma"),
        (
            "pk",
            next((c["name"] for c in observed.get("columns", []) if c.get("pk")), ""),
            "string",
            "pragma",
        ),
        ("row_count_at_qc", str(observed.get("row_count", 0)), "number", "pragma"),
        ("browser_url", browser_url, "string", "pragma"),
        ("match_ok", str(bool(diff.get("match_ok"))).lower(), "bool", "pragma"),
        ("qc_summary", diff.get("summary") or "", "string", "pragma"),
    ]
    if task_id is not None:
        pairs.append(("qc_task_id", str(task_id), "number", "pragma"))
    if vision_id is not None:
        pairs.append(("qc_vision_id", str(vision_id), "number", "vision"))
    if vision_path:
        pairs.append(("vision_path", vision_path, "string", "vision"))

    n = 0
    for keyword, value_text, value_type, source in pairs:
        conn.execute(
            """
            INSERT INTO schema_ssot
                (table_name, keyword, value_text, value_type, source, task_id, version_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(table_name, keyword, source) DO UPDATE SET
                value_text=excluded.value_text,
                value_type=excluded.value_type,
                task_id=excluded.task_id,
                version_id=excluded.version_id,
                updated_at=excluded.updated_at
            """,
            (table, keyword, value_text, value_type, source, task_id, version_id, now),
        )
        n += 1
    return n


def resolve_task(
    conn: sqlite3.Connection, task_label: str | None, table: str
) -> tuple[int | None, int | None]:
    if task_label:
        row = conn.execute(
            "SELECT id, version_id FROM dev_task WHERE task_label = ? ORDER BY id DESC LIMIT 1",
            (task_label,),
        ).fetchone()
        if row:
            return int(row[0]), int(row[1])
    rows = conn.execute(
        """
        SELECT id, version_id, payload_json FROM dev_task
        WHERE status IN ('pending', 'running', 'fail')
        ORDER BY id DESC LIMIT 50
        """
    ).fetchall()
    for r in rows:
        try:
            payload = json.loads(r[2] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("table") == table:
            return int(r[0]), int(r[1])
    return None, None


def insert_qc_run(
    conn: sqlite3.Connection,
    *,
    task_id: int | None,
    table: str,
    browser_url: str,
    expected: list[str] | None,
    observed: dict,
    diff: dict,
    vision_id: int | None = None,
    api_ok: int = 1,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO schema_qc_run
            (task_id, table_name, browser_url, api_ok, vision_id,
             expected_json, observed_json, match_ok, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            table,
            browser_url,
            int(api_ok),
            vision_id,
            json.dumps(expected or [], ensure_ascii=False),
            json.dumps(observed, ensure_ascii=False, default=str),
            1 if diff.get("match_ok") else 0,
            diff.get("summary"),
        ),
    )
    return int(cur.lastrowid)


def maybe_update_task(conn: sqlite3.Connection, task_id: int | None, match_ok: bool) -> None:
    if task_id is None:
        return
    status = "pass" if match_ok else "fail"
    now = _now_iso()
    conn.execute(
        """
        UPDATE dev_task
        SET status = ?, updated_at = ?, completed_at = ?
        WHERE id = ?
        """,
        (status, now, now, task_id),
    )


def set_task_qc_vision(
    conn: sqlite3.Connection, task_id: int | None, vision_id: int | None
) -> None:
    if task_id is None or vision_id is None:
        return
    conn.execute(
        """
        UPDATE dev_task
        SET qc_vision_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (vision_id, _now_iso(), task_id),
    )


def insert_vision_asset(
    conn: sqlite3.Connection,
    path: str,
    *,
    kind: str = "live",
    source: str = "schema_qc_mcp",
    sha256: str | None = None,
) -> int:
    if sha256 is None and path and os.path.isfile(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        sha256 = h.hexdigest()
    cur = conn.execute(
        """
        INSERT INTO vision_asset (kind, path_or_url, sha256, source)
        VALUES (?, ?, ?, ?)
        """,
        (kind, path, sha256, source),
    )
    return int(cur.lastrowid)


def check_api_schema(table: str, timeout: float = 2.0) -> tuple[int, str | None]:
    """Soft HTTP check of db_browser /api/schema. Returns (api_ok 0|1, err)."""
    url = api_schema_url(table)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            if data.get("exists") and data.get("table") == table:
                return 1, None
            return 0, f"unexpected_payload:{data!r}"[:200]
    except Exception as e:
        return 0, f"{type(e).__name__}:{e}"


def open_browser_best_effort(url: str, delay: float) -> str | None:
    """Open URL via traced webbrowser fallback when possible (register_id)."""
    try:
        from openclaw_mcp_trace import traced_webbrowser_open

        inv = traced_webbrowser_open(url, delay=delay, tacid="qc.webbrowser")
        if inv.get("ok"):
            return None
        return f"webbrowser:{inv.get('error') or 'failed'}"
    except Exception:
        try:
            webbrowser.open(url)
        except Exception as e:
            return f"webbrowser:{type(e).__name__}:{e}"
        if delay > 0:
            time.sleep(delay)
        return None


def try_mcp_shot(
    *,
    table: str,
    task_id: int | None,
    browser_url: str,
    open_browser: bool,
    open_delay: float,
) -> tuple[str | None, int | None, str | None]:
    """MCP snapshot → qc_evidence PNG. Returns (path, None yet, error).

    vision_id is inserted by caller with DB connection.
    Uses openclaw_mcp_trace when available (register_id + invoke trace).
    """
    warns: list[str] = []
    if open_browser:
        err = open_browser_best_effort(browser_url, open_delay)
        if err:
            warns.append(err)

    try:
        from mcp_client import extract_image_bytes
    except Exception as e:
        return None, None, f"mcp_import:{type(e).__name__}:{e}"

    try:
        from openclaw_mcp_trace import traced_screen_snapshot

        inv = traced_screen_snapshot(
            tacid=f"qc.shot.{table}",
            task_id=task_id,
        )
        if not inv.get("ok"):
            msg = f"mcp_snapshot:{inv.get('error') or 'failed'}"
            if warns:
                msg = msg + ";" + ";".join(warns)
            return None, None, msg
        result = inv.get("result")
    except Exception as e:
        try:
            from mcp_client import McpClient, McpConfig

            cfg = McpConfig.from_env()
            client = McpClient(cfg)
            result = client.screen_snapshot()
        except ValueError as ve:
            return None, None, f"mcp_config:{ve}"
        except Exception as e2:
            msg = f"mcp_snapshot:{type(e).__name__}:{e}|{type(e2).__name__}:{e2}"
            if warns:
                msg = msg + ";" + ";".join(warns)
            return None, None, msg

    try:
        data, existing_path = extract_image_bytes(result)
        if existing_path and Path(existing_path).is_file():
            # copy into qc_evidence for stable retention
            QC_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            tid = task_id if task_id is not None else 0
            dest = QC_EVIDENCE_DIR / f"qc_{table}_{tid}_{ts}.png"
            dest.write_bytes(Path(existing_path).read_bytes())
            path = str(dest)
        elif data:
            QC_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            tid = task_id if task_id is not None else 0
            dest = QC_EVIDENCE_DIR / f"qc_{table}_{tid}_{ts}.png"
            dest.write_bytes(data)
            path = str(dest)
        else:
            msg = "mcp_snapshot:no_image_bytes"
            if warns:
                msg = msg + ";" + ";".join(warns)
            return None, None, msg
        if warns:
            return path, None, "WARN:" + ";".join(warns)
        return path, None, None
    except Exception as e:
        msg = f"mcp_snapshot:{type(e).__name__}:{e}"
        if warns:
            msg = msg + ";" + ";".join(warns)
        return None, None, msg


def try_notify(title: str, body: str) -> tuple[bool, str | None]:
    try:
        from openclaw_mcp_trace import traced_notify

        inv = traced_notify(title, body, tacid="qc.notify")
        if inv.get("ok"):
            return True, None
        return False, f"mcp_notify:{inv.get('error') or 'failed'}"
    except Exception as e:
        try:
            from mcp_client import McpClient, McpConfig

            cfg = McpConfig.from_env()
            client = McpClient(cfg)
            client.notify(title, body)
            return True, None
        except Exception as e2:
            return False, f"mcp_notify:{type(e).__name__}:{e}|{type(e2).__name__}:{e2}"


def spawn_detached_qc(argv: list[str], log_name: str | None = None) -> None:
    """Fire-and-forget schema_qc child (Windows detached)."""
    QC_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    if log_name is None:
        log_name = f"qc_spawn_{int(time.time() * 1000)}.log"
    log_path = QC_EVIDENCE_DIR / log_name
    env = os.environ.copy()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    try:
        log_f = open(log_path, "a", encoding="utf-8")
        subprocess.Popen(
            argv,
            cwd=str(BASE_DIR),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        print(f"qc spawned: {' '.join(argv)} log={log_path}")
    except Exception as e:
        print(f"qc spawn failed: {e}")


def list_pending_qc_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All pending/fail/running qc.verify_schema tasks with payload table."""
    rows = conn.execute(
        """
        SELECT t.id, t.task_label, t.status, t.payload_json, a.code AS action_code
        FROM dev_task t
        JOIN task_action_name a ON a.id = t.action_name_id
        WHERE a.code = 'qc.verify_schema'
          AND t.status IN ('pending', 'fail', 'running')
        ORDER BY t.id
        """
    ).fetchall()
    out = []
    for r in rows:
        table = None
        browser_url = None
        try:
            payload = json.loads(r["payload_json"] or "{}")
            table = payload.get("table")
            browser_url = payload.get("browser_url")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "id": int(r["id"]),
                "task_label": r["task_label"],
                "status": r["status"],
                "table": table,
                "browser_url": browser_url,
                "payload": payload,
            }
        )
    return out


def build_qc_argv_for_task(
    task: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    with_mcp: bool = False,
    write_ssot: bool = True,
    python_exe: str | None = None,
) -> list[str]:
    """Build schema_qc.py argv from a qc.verify_schema task dict."""
    import sys

    table = task.get("table")
    if not table:
        raise ValueError("task has no payload.table")
    py = python_exe or sys.executable
    script = str(BASE_DIR / "schema_qc.py")
    db = str(db_path or DB_PATH)
    argv = [py, script, "--table", str(table), "--db", db]
    if write_ssot:
        argv.append("--write-ssot")
    label = task.get("task_label")
    if label:
        argv.extend(["--task-label", str(label)])
    browser = task.get("browser_url")
    if browser:
        argv.extend(["--browser-url", str(browser)])
    if with_mcp:
        argv.append("--mcp-shot")
    exp = (task.get("payload") or {}).get("expected_columns")
    if isinstance(exp, list) and exp:
        argv.extend(["--expected", ",".join(str(x) for x in exp)])
    elif isinstance(exp, str) and exp.strip():
        argv.extend(["--expected", exp.strip()])
    return argv


def load_qc_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
    """Load one dev_task as QC target (qc.verify_schema or parent of remediate)."""
    row = conn.execute(
        """
        SELECT t.id, t.task_label, t.status, t.payload_json, t.parent_task_id,
               a.code AS action_code
        FROM dev_task t
        LEFT JOIN task_action_name a ON a.id = t.action_name_id
        WHERE t.id = ?
        """,
        (int(task_id),),
    ).fetchone()
    if not row:
        return None
    if hasattr(row, "keys"):
        tid = int(row["id"])
        label = row["task_label"]
        status = row["status"]
        raw = row["payload_json"]
        parent_id = row["parent_task_id"]
        code = row["action_code"]
    else:
        tid, label, status, raw, parent_id, code = (
            int(row[0]),
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
        )
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # If remediate child, climb to QC parent for gate re-run
    if code == "schema.remediate" and parent_id is not None:
        return load_qc_task(conn, int(parent_id))

    table = payload.get("table")
    return {
        "id": tid,
        "task_label": label,
        "status": status,
        "table": table,
        "browser_url": payload.get("browser_url"),
        "payload": payload,
        "action_code": code,
    }


def run_qc_for_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    db_path: str | Path | None = None,
    with_mcp: bool = False,
    write_ssot: bool = True,
    detached: bool = True,
) -> dict[str, Any]:
    """Run or spawn schema QC for a task. Gate remains PRAGMA-only inside schema_qc.

    CH5: entry is traced via code_health.function_invoker (pipeline C, never a gate).
    Invoker records spawn/sync launch outcome — not PRAGMA match_ok.
    """
    task = load_qc_task(conn, int(task_id))
    if not task:
        raise ValueError(f"unknown task_id: {task_id}")
    if not task.get("table"):
        raise ValueError(f"task {task_id} has no payload.table for QC")
    if task.get("action_code") not in (None, "qc.verify_schema") and task.get(
        "action_code"
    ) != "qc.verify_schema":
        # still allow if table present
        pass
    argv = build_qc_argv_for_task(
        task,
        db_path=db_path or DB_PATH,
        with_mcp=with_mcp,
        write_ssot=write_ssot,
    )
    tacid = str(task.get("task_label") or f"qc.task.{task['id']}").strip()
    qc_task_id = int(task["id"])

    def _run_body() -> dict[str, Any]:
        if detached:
            log_name = f"qc_run_task_{task.get('task_label') or task_id}.log"
            spawn_detached_qc(argv, log_name=log_name)
            return {
                "ok": True,
                "mode": "detached",
                "task_id": qc_task_id,
                "task_label": task.get("task_label"),
                "table": task.get("table"),
                "argv": argv[1:],  # drop python exe for readability
                "log_name": log_name,
                "with_mcp": with_mcp,
                "gate": "pragma_exact_set",
            }
        # sync subprocess
        try:
            proc = subprocess.run(
                argv,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            return {
                "ok": proc.returncode == 0,
                "mode": "sync",
                "task_id": qc_task_id,
                "task_label": task.get("task_label"),
                "table": task.get("table"),
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-4000:],
                "stderr_tail": (proc.stderr or "")[-2000:],
                "with_mcp": with_mcp,
                "gate": "pragma_exact_set",
            }
        except Exception as e:
            return {
                "ok": False,
                "mode": "sync",
                "task_id": qc_task_id,
                "error": f"{type(e).__name__}:{e}",
                "gate": "pragma_exact_set",
            }

    # Pipeline C telemetry (W8) — does not change PRAGMA gate
    try:
        from code_health import function_invoker

        inv = function_invoker(
            _run_body,
            tacid=tacid,
            module_name="schema_qc",
            function_name="run_qc_for_task",
            task_id=qc_task_id,
            source="schema_qc.run_qc_for_task",
            conn=conn,
            reraise=True,
            commit=True,
        )
        result = inv.get("result") if isinstance(inv.get("result"), dict) else {
            "ok": bool(inv.get("ok")),
            "error": inv.get("error"),
            "gate": "pragma_exact_set",
        }
        result = dict(result)
        result["code_health"] = {
            "gate": "never",
            "invoker": True,
            "trace_id": (inv.get("invoke") or {}).get("trace_id"),
            "tacid": tacid,
            "why": ["W8", "W2"],
            "note": "trace is launch outcome; structure gate remains PRAGMA-only",
        }
        return result
    except ImportError:
        out = _run_body()
        out["code_health"] = {"gate": "never", "invoker": False}
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Schema QC against agent.db")
    ap.add_argument("--table", required=True, help="Table name to QC")
    ap.add_argument(
        "--expected",
        default="",
        help="Comma-separated expected column names (order optional for pass)",
    )
    ap.add_argument("--task-label", default=None, help="dev_task.task_label e.g. 1.1b")
    ap.add_argument("--write-ssot", action="store_true", help="Upsert schema_ssot + qc_run")
    ap.add_argument("--browser-url", default=None)
    ap.add_argument(
        "--mcp-shot",
        action="store_true",
        help="Opt-in MCP screen.snapshot proof (does not change PRAGMA match_ok)",
    )
    ap.add_argument(
        "--open-browser",
        action="store_true",
        default=False,
        help="Open browser_url before MCP shot (default on with --mcp-shot)",
    )
    ap.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Disable webbrowser.open even with --mcp-shot",
    )
    ap.add_argument(
        "--open-delay",
        type=float,
        default=DEFAULT_OPEN_DELAY,
        help=f"Seconds to wait after opening browser (default {DEFAULT_OPEN_DELAY})",
    )
    ap.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip MCP system.notify (default: always try notify)",
    )
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    db_path = Path(args.db)
    if ensure_schema is not None:
        try:
            ensure_schema(str(db_path))
        except Exception as e:
            print(f"schema ensure skipped: {e}")

    expected = [c.strip() for c in args.expected.split(",") if c.strip()] or None
    if expected is None and args.table == "vision_asset":
        expected = ["id", "kind", "path_or_url", "sha256", "source", "created_at"]

    browser_url = args.browser_url or default_browser_url(args.table)
    do_open = False
    if args.mcp_shot and not args.no_open_browser:
        do_open = True
    if args.open_browser:
        do_open = True
    if args.no_open_browser:
        do_open = False

    conn = connect(db_path)
    try:
        observed = observe_table(conn, args.table)
        diff = diff_expected(observed, expected)
        task_id, version_id = resolve_task(conn, args.task_label, args.table)

        api_ok, api_err = check_api_schema(args.table)
        if api_err:
            print(f"api_schema: ok=0 ({api_err})")
        else:
            print("api_schema: ok=1")

        vision_id = None
        vision_path = None
        shot_err = None
        if args.mcp_shot:
            vision_path, _, shot_err = try_mcp_shot(
                table=args.table,
                task_id=task_id,
                browser_url=browser_url,
                open_browser=do_open,
                open_delay=float(args.open_delay),
            )
            if vision_path:
                try:
                    vision_id = insert_vision_asset(
                        conn,
                        vision_path,
                        kind="live",
                        source="schema_qc_mcp",
                    )
                    # keep uncommitted until write-ssot block or commit below
                except Exception as e:
                    shot_err = (shot_err + ";" if shot_err else "") + f"vision_insert:{e}"
                    vision_id = None
            # Assist only — NEVER mutate gate summary / match_ok
            if shot_err:
                warn = shot_err if shot_err.startswith("WARN:") else f"WARN:{shot_err}"
                print(f"mcp_shot: {warn} (non-gate)")
            else:
                print(f"mcp_shot: path={vision_path} vision_id={vision_id}")

        hard_pass = bool(diff.get("match_ok") and observed.get("exists"))

        print("=== Schema QC (HARD GATE) ===")
        print(f"table: {args.table}")
        print(f"exists: {observed.get('exists')}")
        print(f"columns ({observed.get('column_count')}): {observed.get('column_names')}")
        print(f"row_count: {observed.get('row_count')}")
        print(f"browser_url: {browser_url}")
        print(f"expected: {expected}")
        print(f"task_id: {task_id} version_id: {version_id}")
        print(f"vision_id: {vision_id} path: {vision_path}")
        print(f"gate: {'PASS' if hard_pass else 'FAIL'} | {diff['summary']}")
        if diff.get("missing_columns"):
            print("MISSING:", diff["missing_columns"])
        if diff.get("extra_columns"):
            print("EXTRA:", diff["extra_columns"])
        if api_err:
            print(f"telemetry api_ok=0 (non-gate): {api_err}")
        if shot_err:
            print(f"telemetry mcp_shot (non-gate): {shot_err}")

        run_id = None
        fault_info = None

        # Always write qc_run on hard fail; ssot when --write-ssot or fail
        write_run = args.write_ssot or (not hard_pass)
        if write_run:
            if args.write_ssot and not _table_exists(conn, "schema_ssot"):
                print("ERROR: schema_ssot missing — run python create_db.py --migrate")
                return 2
            if args.write_ssot and _table_exists(conn, "schema_ssot"):
                n = upsert_schema_ssot(
                    conn,
                    args.table,
                    observed,
                    diff,
                    task_id,
                    version_id,
                    browser_url,
                    vision_id=vision_id,
                    vision_path=vision_path,
                )
                print(f"wrote schema_ssot keys={n}")
            if _table_exists(conn, "schema_qc_run"):
                run_id = insert_qc_run(
                    conn,
                    task_id=task_id,
                    table=args.table,
                    browser_url=browser_url,
                    expected=expected,
                    observed=observed,
                    diff=diff,
                    vision_id=vision_id,
                    api_ok=api_ok,
                )
                print(f"qc_run_id={run_id}")
            if task_id is not None:
                maybe_update_task(conn, task_id, hard_pass)
                set_task_qc_vision(conn, task_id, vision_id)
            conn.commit()
        elif vision_id is not None:
            conn.commit()
            print(f"persisted vision_asset id={vision_id} (no ssot write)")

        # HARD FAIL → fault_event + remediation task (any hard fail; no other gates)
        if not hard_pass:
            if record_schema_qc_hard_fail is None:
                print("ERROR: record_schema_qc_hard_fail unavailable")
            else:
                try:
                    fault_info = record_schema_qc_hard_fail(
                        conn,
                        table=args.table,
                        diff=diff,
                        observed=observed,
                        expected=expected,
                        qc_task_id=task_id,
                        qc_run_id=run_id,
                        browser_url=browser_url,
                        vision_id=vision_id,
                        version_id=version_id,
                    )
                    print(
                        "hard_fail_recorded: "
                        f"event_id={fault_info.get('event_id')} "
                        f"remediation_task_id={fault_info.get('remediation_task_id')} "
                        f"label={fault_info.get('remediation_task_label')} "
                        f"goal_type={fault_info.get('goal_type')}"
                    )
                    if fault_info.get("goal_text"):
                        print(f"goal_text: {fault_info.get('goal_text')}")
                    # FH4 hint only — STEP2 is never a gate and not auto-run (Ollama slow)
                    rid = fault_info.get("remediation_task_id")
                    if rid is not None:
                        vid = fault_info.get("vision_id") or vision_id
                        extra = f" --vision-id {vid}" if vid is not None else ""
                        print(
                            "fail_handling STEP2 (optional, non-gate): "
                            f"python fail_handling.py vision --task-id {rid}{extra}"
                        )
                except Exception as e:
                    print(f"hard_fail_record ERROR (gate still FAIL): {type(e).__name__}:{e}")

        # Always try notify unless --no-notify (telemetry, not a gate)
        if not args.no_notify:
            status = "PASS" if hard_pass else "FAIL"
            title = f"Schema QC {status}: {args.table}"
            body_lines = [
                diff.get("summary") or "",
                f"task_label={args.task_label} task_id={task_id}",
                f"browser={browser_url}",
                f"vision_id={vision_id}",
                f"api_ok={api_ok} (non-gate)",
            ]
            if fault_info:
                body_lines.append(
                    f"fault_event_id={fault_info.get('event_id')} "
                    f"remediate={fault_info.get('remediation_task_label')} "
                    f"#{fault_info.get('remediation_task_id')}"
                )
            ok, nerr = try_notify(title, "\n".join(body_lines))
            if ok:
                print("notify: ok")
            else:
                print(f"notify: skipped/fail ({nerr})")

        return 0 if hard_pass else 1
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (name,),
        ).fetchone()
        is not None
    )


if __name__ == "__main__":
    raise SystemExit(main())
