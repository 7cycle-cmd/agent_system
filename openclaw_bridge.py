"""Fault → vision analysis → optional MCP live snapshot → notify (v1 notify-only).

Iron rule: never raise into watchdog trunk. Analysis/MCP/Ollama failures are
recorded on fault_analysis / watchdog_log only.

Usage:
  python openclaw_bridge.py --event-id 12
  python openclaw_bridge.py --event-id 12 --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "agent.db")
FAULT_EVIDENCE_DIR = BASE_DIR / "fault_evidence"

from db_schema import ensure_fault_analysis_schema  # noqa: E402
from vision_analyze import analyze_evidence  # noqa: E402


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    ensure_fault_analysis_schema(conn)
    return conn


def load_fault(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT event_id, worker_id, group_id, fault_type, status,
               detect_at, evidence_img_path, evidence_error
        FROM fault_event WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None
    keys = [
        "event_id", "worker_id", "group_id", "fault_type", "status",
        "detect_at", "evidence_img_path", "evidence_error",
    ]
    return dict(zip(keys, row))


def has_analysis(conn: sqlite3.Connection, event_id: int) -> bool:
    n = conn.execute(
        "SELECT 1 FROM fault_analysis WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    return n is not None


def write_log(conn: sqlite3.Connection, worker_id: int | None, message: str, level: str = "INFO"):
    if worker_id is None:
        # watchdog_log.worker_id is NOT NULL — use 0 sentinel only if needed; skip if unknown
        return
    conn.execute(
        "INSERT INTO watchdog_log (worker_id, message, level, created_at) VALUES (?, ?, ?, ?)",
        (worker_id, message, level, datetime.datetime.now()),
    )


def insert_analysis(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    model: str | None,
    summary: str | None,
    detail_json: str | None,
    evidence_used: str,
    evidence_path: str | None,
    error: str | None,
    notified_at: datetime.datetime | None,
    option_id: int | None = None,
    solution_id: int | None = None,
    match_score: float | None = None,
    match_status: str | None = None,
    ssot_prompt_json: str | None = None,
    solution_summary: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fault_analysis
            (event_id, model, summary, detail_json, evidence_used,
             evidence_path, error, notified_at, created_at,
             option_id, solution_id, match_score, match_status,
             ssot_prompt_json, solution_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            model,
            summary,
            detail_json,
            evidence_used,
            evidence_path,
            error,
            notified_at,
            datetime.datetime.now(),
            option_id,
            solution_id,
            match_score,
            match_status,
            ssot_prompt_json,
            solution_summary,
        ),
    )


def try_ssot_match(conn: sqlite3.Connection, event_id: int, *, summary: str | None = None) -> dict | None:
    """FH6: match fault_option SSOT after analysis. Never raises into trunk."""
    try:
        from fail_handling import run_fault_ssot_match
    except Exception as e:
        return {"error": f"import_fail_handling:{type(e).__name__}:{e}"}
    try:
        # refresh catalog seeds (idempotent)
        try:
            from db_schema import seed_ssot_defaults

            seed_ssot_defaults(conn)
        except Exception:
            pass
        return run_fault_ssot_match(
            conn,
            case_id=int(event_id),
            write_event=True,
            write_analysis=True,
            write_facts=True,
            write_payload=False,
            commit=False,
            summary=summary,
        )
    except Exception as e:
        return {"error": f"ssot_match:{type(e).__name__}:{e}"}


def resolve_frozen_path(fault: dict) -> tuple[str | None, str]:
    """Return (path, evidence_used) for frozen evidence if usable."""
    path = fault.get("evidence_img_path")
    err = fault.get("evidence_error") or ""
    if path and Path(path).is_file():
        # FALLBACK still counts as frozen evidence (honest note already on fault)
        return path, "frozen"
    if err.startswith("EVIDENCE_MISSING") or err.startswith("copy_failed") or not path:
        return None, "none"
    if path and not Path(path).is_file():
        return None, "none"
    return None, "none"


def try_live_snapshot(event_id: int) -> tuple[str | None, str | None]:
    """Call Companion Local MCP screen.snapshot; save under fault_evidence/.

    Returns (path, error). Uses openclaw_mcp_trace when available (register_id).
    """
    try:
        from mcp_client import extract_image_bytes
    except Exception as e:
        return None, f"mcp_import:{type(e).__name__}"

    try:
        from openclaw_mcp_trace import traced_screen_snapshot

        inv = traced_screen_snapshot(tacid=f"bridge.live.{event_id}")
        if not inv.get("ok"):
            err = inv.get("error") or "mcp_snapshot:failed"
            return None, f"mcp_snapshot:{err}"
        result = inv.get("result")
    except Exception as e:
        # Soft fallback: direct client (still gate=never)
        try:
            from mcp_client import McpClient, McpConfig

            cfg = McpConfig.from_env()
            client = McpClient(cfg)
            result = client.screen_snapshot()
        except ValueError as ve:
            return None, f"mcp_config:{ve}"
        except Exception as e2:
            return None, f"mcp_snapshot:{type(e).__name__}:{e}|{type(e2).__name__}:{e2}"

    try:
        data, existing_path = extract_image_bytes(result)
        if existing_path and Path(existing_path).is_file():
            return existing_path, None
        if not data:
            return None, "mcp_snapshot:no_image_bytes"
        FAULT_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        dest = FAULT_EVIDENCE_DIR / f"fault_evt_{event_id}_live_{int(time.time() * 1000)}.png"
        dest.write_bytes(data)
        return str(dest), None
    except Exception as e:
        return None, f"mcp_snapshot:{type(e).__name__}:{e}"


def try_notify(title: str, body: str) -> tuple[bool, str | None]:
    try:
        from openclaw_mcp_trace import traced_notify

        inv = traced_notify(title, body, tacid="bridge.notify")
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


def process_event(event_id: int, *, dry_run: bool = False, db_path: str = DB_PATH) -> int:
    conn = get_conn(db_path)
    try:
        fault = load_fault(conn, event_id)
        if not fault:
            print(f"bridge: event_id={event_id} not found")
            return 2
        if has_analysis(conn, event_id):
            print(f"bridge: event_id={event_id} already analyzed — skip")
            return 0

        path, used = resolve_frozen_path(fault)
        live_err = None
        if path is None:
            if dry_run:
                print("bridge: dry-run would attempt live MCP snapshot")
            else:
                path, live_err = try_live_snapshot(event_id)
                if path:
                    used = "live"
                else:
                    used = "none"

        if dry_run:
            print(f"bridge dry-run: evidence_used={used} path={path} live_err={live_err}")
            print(f"  fault={fault}")
            return 0

        vision = analyze_evidence(
            path,
            fault_type=fault["fault_type"],
            worker_id=fault["worker_id"],
            group_id=fault["group_id"],
            evidence_error=fault.get("evidence_error"),
            event_id=event_id,
        )

        errors: list[str] = []
        if live_err:
            errors.append(live_err)
        if vision.error:
            errors.append(vision.error)

        title = f"Fault #{event_id} [{fault['fault_type']}]"
        body = vision.summary or "(no summary)"
        if used == "none":
            body = f"[no evidence image] {body}"
        ok, notify_err = try_notify(title, body)
        notified_at = datetime.datetime.now() if ok else None
        if notify_err:
            errors.append(notify_err)

        # FH6 SSOT match before insert so analysis row carries option/score
        match_info = try_ssot_match(conn, event_id, summary=vision.summary)
        match_err = None
        if isinstance(match_info, dict) and match_info.get("error"):
            match_err = str(match_info.get("error"))
            errors.append(match_err)
            match_info = None

        detail = {
            "vision": vision.detail,
            "raw_preview": (vision.raw_text or "")[:2000],
            "fault": {
                "fault_type": fault["fault_type"],
                "worker_id": fault["worker_id"],
                "group_id": fault["group_id"],
                "evidence_error": fault.get("evidence_error"),
            },
            "evidence_used": used,
            "notify_ok": ok,
            "fh6_match": {
                "match_status": (match_info or {}).get("match_status"),
                "match_score": (match_info or {}).get("match_score"),
                "option_id": (match_info or {}).get("option_id"),
                "option_code": (match_info or {}).get("option_code"),
                "solution_id": (match_info or {}).get("solution_id"),
                "gate": "never",
            }
            if match_info
            else {"error": match_err, "gate": "never"},
        }
        err_join = "; ".join(errors) if errors else None

        try:
            # If FH6 already inserted a lightweight analysis row, update it;
            # otherwise insert full vision analysis with match fields.
            existing_a = conn.execute(
                "SELECT id FROM fault_analysis WHERE event_id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
            opt_id = (match_info or {}).get("option_id")
            sol_id = (match_info or {}).get("solution_id")
            m_score = (match_info or {}).get("match_score")
            m_status = (match_info or {}).get("match_status")
            prompt_json = None
            sol_sum = None
            if match_info:
                try:
                    prompt_json = json.dumps(match_info.get("ssot_prompt") or {}, ensure_ascii=False)
                except Exception:
                    prompt_json = None
                sol_sum = match_info.get("solution_title") or match_info.get("summary")

            if existing_a:
                conn.execute(
                    """
                    UPDATE fault_analysis
                    SET model = COALESCE(?, model),
                        summary = COALESCE(?, summary),
                        detail_json = ?,
                        evidence_used = ?,
                        evidence_path = COALESCE(?, evidence_path),
                        error = ?,
                        notified_at = COALESCE(?, notified_at),
                        option_id = COALESCE(?, option_id),
                        solution_id = COALESCE(?, solution_id),
                        match_score = COALESCE(?, match_score),
                        match_status = COALESCE(?, match_status),
                        ssot_prompt_json = COALESCE(?, ssot_prompt_json),
                        solution_summary = COALESCE(?, solution_summary)
                    WHERE event_id = ?
                    """,
                    (
                        vision.model,
                        vision.summary,
                        json.dumps(detail, ensure_ascii=False),
                        used if used in ("frozen", "live", "none") else "none",
                        path,
                        err_join,
                        notified_at,
                        int(opt_id) if opt_id is not None else None,
                        int(sol_id) if sol_id is not None else None,
                        m_score,
                        m_status,
                        prompt_json,
                        sol_sum,
                        event_id,
                    ),
                )
            else:
                insert_analysis(
                    conn,
                    event_id=event_id,
                    model=vision.model,
                    summary=vision.summary,
                    detail_json=json.dumps(detail, ensure_ascii=False),
                    evidence_used=used if used in ("frozen", "live", "none") else "none",
                    evidence_path=path,
                    error=err_join,
                    notified_at=notified_at,
                    option_id=int(opt_id) if opt_id is not None else None,
                    solution_id=int(sol_id) if sol_id is not None else None,
                    match_score=m_score,
                    match_status=m_status,
                    ssot_prompt_json=prompt_json,
                    solution_summary=sol_sum,
                )
            write_log(
                conn,
                fault["worker_id"],
                (
                    f"openclaw_bridge event_id={event_id} used={used} notify={ok} "
                    f"match={(match_info or {}).get('match_status')}/"
                    f"{(match_info or {}).get('match_score')} "
                    f"opt={(match_info or {}).get('option_code')} err={err_join or '-'}"
                ),
                "INFO" if not err_join or ok else "WARN",
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # race: another bridge wrote first
            conn.rollback()
            print(f"bridge: event_id={event_id} analysis race — already inserted")
            return 0

        print(
            f"bridge OK event_id={event_id} used={used} notify={ok} "
            f"match={(match_info or {}).get('match_status')} "
            f"score={(match_info or {}).get('match_score')} "
            f"option={(match_info or {}).get('option_code')} "
            f"summary={vision.summary!r} err={err_join}"
        )
        return 0 if (vision.error is None or path is not None or ok) else 1
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"bridge FAIL event_id={event_id}: {e}")
        traceback.print_exc()
        return 1
    finally:
        conn.close()


def spawn_detached(event_id: int, db_path: str | None = None) -> None:
    """Fire-and-forget subprocess so watchdog scan is never blocked."""
    import subprocess

    py = sys.executable
    script = str(BASE_DIR / "openclaw_bridge.py")
    args = [py, script, "--event-id", str(event_id)]
    if db_path:
        args.extend(["--db", db_path])
    env = os.environ.copy()
    # Detached on Windows
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    try:
        subprocess.Popen(
            args,
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        print(f"bridge spawned for event_id={event_id}")
    except Exception as e:
        print(f"bridge spawn failed event_id={event_id}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw fault analysis bridge")
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return process_event(args.event_id, dry_run=args.dry_run, db_path=args.db)


if __name__ == "__main__":
    raise SystemExit(main())
