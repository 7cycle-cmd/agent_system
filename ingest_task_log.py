"""
Ingest qc_evidence plan/agent_log/qc_report JSON triples into task_run_logs.

CLI:
  --scan              scan and import new complete triples
  --rescan [TASK_ID ...]  re-read triples and UPDATE existing rows (all if no ids)
  --dry-run           with --scan or --rescan: validate/show only; no DB write; no marker update
  --list              list task_ids from qc_evidence/.ingested_tasks.json

CREATE TABLE IF NOT EXISTS task_run_logs lives in this script only (not init_db/db_schema).
Does not modify protocol templates or validate_new_task.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
QC_EVIDENCE = BASE_DIR / "qc_evidence"
DB_PATH = BASE_DIR / "agent.db"
MARKER_PATH = QC_EVIDENCE / ".ingested_tasks.json"
INGEST_LOGS_DIR = QC_EVIDENCE / "ingest_logs"

FILE_RE = re.compile(r"^(plan|agent_log|qc_report)_(.+)\.json$", re.IGNORECASE)

PLAN_REQUIRED = ("task_id", "scope", "step_list")
AGENT_REQUIRED = ("task_id", "files_modified", "commands")
QC_REQUIRED = ("task_id", "checklist_items", "final_verdict")

DEFAULT_SKILL_NAME = "ide_execution_worker"
DEFAULT_SKILL_VERSION = "1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    QC_EVIDENCE.mkdir(parents=True, exist_ok=True)
    INGEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _load_marker() -> dict[str, Any]:
    _ensure_dirs()
    if not MARKER_PATH.is_file():
        data = {"task_ids": [], "updated_at": None}
        MARKER_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data
    try:
        raw = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if isinstance(raw, list):
        return {"task_ids": [str(x) for x in raw], "updated_at": None}
    if not isinstance(raw, dict):
        return {"task_ids": [], "updated_at": None}
    ids = raw.get("task_ids") or raw.get("ingested") or []
    if not isinstance(ids, list):
        ids = []
    return {
        "task_ids": [str(x) for x in ids],
        "updated_at": raw.get("updated_at"),
    }


def _save_marker(data: dict[str, Any]) -> None:
    _ensure_dirs()
    out = {
        "task_ids": sorted(set(str(x) for x in (data.get("task_ids") or []))),
        "updated_at": _utc_now(),
    }
    MARKER_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _append_ingest_log(events: list[dict[str, Any]], mode: str) -> Path:
    _ensure_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = INGEST_LOGS_DIR / f"ingest_{ts}.json"
    payload = {
        "mode": mode,
        "written_at": _utc_now(),
        "events": events,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # also append jsonl for streaming consumers
    jsonl = INGEST_LOGS_DIR / "ingest.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(
                json.dumps({"written_at": payload["written_at"], **ev}, ensure_ascii=False)
                + "\n"
            )
    return path


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_task_run_logs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_run_logs (
            task_id TEXT PRIMARY KEY,
            skill_name TEXT,
            skill_version TEXT,
            scope TEXT,
            step_list TEXT,
            files_modified TEXT,
            commands TEXT,
            qc_checklist_items TEXT,
            final_verdict TEXT,
            ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _discover_groups() -> dict[str, dict[str, Path]]:
    """Top-level qc_evidence only: kind -> path per task_id."""
    groups: dict[str, dict[str, Path]] = {}
    if not QC_EVIDENCE.is_dir():
        return groups
    for p in QC_EVIDENCE.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        if p.name.startswith("."):
            continue
        m = FILE_RE.match(p.name)
        if not m:
            continue
        kind = m.group(1).lower()
        tid = m.group(2)
        groups.setdefault(tid, {})[kind] = p
    return groups


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"json_error: {e}"
    if not isinstance(data, dict):
        return None, "json_root_not_object"
    return data, None


def _require_keys(
    data: dict[str, Any], keys: tuple[str, ...], label: str
) -> list[str]:
    missing = [k for k in keys if k not in data]
    return [f"{label} missing field: {k}" for k in missing]


def _validate_triple(
    task_id: str, paths: dict[str, Path]
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    need = ("plan", "agent_log", "qc_report")
    for k in need:
        if k not in paths:
            errors.append(f"missing_file: {k}_{task_id}.json")
    if errors:
        return None, errors

    plan, e1 = _load_json(paths["plan"])
    agent, e2 = _load_json(paths["agent_log"])
    qc, e3 = _load_json(paths["qc_report"])
    if e1:
        errors.append(f"plan: {e1}")
    if e2:
        errors.append(f"agent_log: {e2}")
    if e3:
        errors.append(f"qc_report: {e3}")
    if errors or plan is None or agent is None or qc is None:
        return None, errors

    errors.extend(_require_keys(plan, PLAN_REQUIRED, "plan"))
    errors.extend(_require_keys(agent, AGENT_REQUIRED, "agent_log"))
    errors.extend(_require_keys(qc, QC_REQUIRED, "qc_report"))

    for label, data in (("plan", plan), ("agent_log", agent), ("qc_report", qc)):
        inner = str(data.get("task_id") or "").strip()
        if inner and inner != task_id:
            errors.append(f"{label} task_id mismatch: file={task_id!r} json={inner!r}")

    if errors:
        return None, errors

    skill_name = (
        str(plan.get("skill_name") or agent.get("skill_name") or "").strip()
        or str(plan.get("checklist_id") or "").strip()
        or DEFAULT_SKILL_NAME
    )
    skill_version = (
        str(plan.get("skill_version") or agent.get("skill_version") or "").strip()
        or DEFAULT_SKILL_VERSION
    )

    merged = {
        "task_id": task_id,
        "skill_name": skill_name,
        "skill_version": skill_version,
        "scope": plan.get("scope"),
        "step_list": plan.get("step_list"),
        "files_modified": agent.get("files_modified"),
        "commands": agent.get("commands"),
        "qc_checklist_items": qc.get("checklist_items"),
        "final_verdict": qc.get("final_verdict"),
        "paths": {k: str(v) for k, v in paths.items()},
    }
    return merged, []


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _row_exists(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM task_run_logs WHERE task_id = ?", (task_id,)
    ).fetchone()
    return bool(row)


def _insert_row(conn: sqlite3.Connection, merged: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO task_run_logs (
            task_id, skill_name, skill_version, scope, step_list,
            files_modified, commands, qc_checklist_items, final_verdict, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            merged["task_id"],
            merged.get("skill_name"),
            merged.get("skill_version"),
            _dumps(merged.get("scope")),
            _dumps(merged.get("step_list")),
            _dumps(merged.get("files_modified")),
            _dumps(merged.get("commands")),
            _dumps(merged.get("qc_checklist_items")),
            str(merged.get("final_verdict") or ""),
            _utc_now(),
        ),
    )
    conn.commit()


def _update_row(conn: sqlite3.Connection, merged: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE task_run_logs SET
            skill_name = ?,
            skill_version = ?,
            scope = ?,
            step_list = ?,
            files_modified = ?,
            commands = ?,
            qc_checklist_items = ?,
            final_verdict = ?,
            ingested_at = ?
        WHERE task_id = ?
        """,
        (
            merged.get("skill_name"),
            merged.get("skill_version"),
            _dumps(merged.get("scope")),
            _dumps(merged.get("step_list")),
            _dumps(merged.get("files_modified")),
            _dumps(merged.get("commands")),
            _dumps(merged.get("qc_checklist_items")),
            str(merged.get("final_verdict") or ""),
            _utc_now(),
            merged["task_id"],
        ),
    )
    conn.commit()


def cmd_list() -> int:
    marker = _load_marker()
    ids = marker.get("task_ids") or []
    print(json.dumps({"task_ids": ids, "count": len(ids), "marker": str(MARKER_PATH)}, indent=2))
    return 0


def cmd_scan(*, dry_run: bool) -> int:
    events: list[dict[str, Any]] = []
    mode = "scan_dry_run" if dry_run else "scan"
    marker = _load_marker()
    ingested = set(marker.get("task_ids") or [])
    groups = _discover_groups()

    summary = {
        "mode": mode,
        "discovered_task_ids": sorted(groups.keys()),
        "candidates": [],
        "imported": [],
        "skipped": [],
        "invalid": [],
    }

    conn: sqlite3.Connection | None = None
    try:
        if not dry_run:
            conn = _connect()
            ensure_task_run_logs_table(conn)

        for task_id in sorted(groups.keys()):
            paths = groups[task_id]
            kinds = sorted(paths.keys())
            entry = {"task_id": task_id, "kinds": kinds, "paths": {k: str(p) for k, p in paths.items()}}

            if task_id in ingested:
                summary["skipped"].append({**entry, "reason": "already_in_marker"})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "skip",
                        "detail": "already_in_marker",
                    }
                )
                continue

            merged, errors = _validate_triple(task_id, paths)
            if errors:
                summary["invalid"].append({**entry, "errors": errors})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "invalid",
                        "detail": errors,
                    }
                )
                continue

            assert merged is not None
            summary["candidates"].append(
                {
                    "task_id": task_id,
                    "final_verdict": merged.get("final_verdict"),
                    "skill_name": merged.get("skill_name"),
                    "paths": merged.get("paths"),
                }
            )

            if dry_run:
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "would_insert",
                        "detail": {
                            "final_verdict": merged.get("final_verdict"),
                            "skill_name": merged.get("skill_name"),
                        },
                    }
                )
                continue

            assert conn is not None
            if _row_exists(conn, task_id):
                # row exists but not in marker — adopt marker, no duplicate insert
                ingested.add(task_id)
                summary["skipped"].append({**entry, "reason": "already_in_db"})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "skip",
                        "detail": "already_in_db",
                    }
                )
                continue

            try:
                _insert_row(conn, merged)
                ingested.add(task_id)
                summary["imported"].append(task_id)
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "insert",
                        "detail": {"final_verdict": merged.get("final_verdict")},
                    }
                )
            except sqlite3.IntegrityError as e:
                summary["skipped"].append({**entry, "reason": f"integrity: {e}"})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "skip",
                        "detail": f"integrity: {e}",
                    }
                )

        if not dry_run:
            marker["task_ids"] = sorted(ingested)
            _save_marker(marker)

        log_path = _append_ingest_log(events, mode=mode)
        summary["ingest_log"] = str(log_path)
        summary["marker_path"] = str(MARKER_PATH)
        summary["dry_run"] = dry_run
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        events.append(
            {
                "ts": _utc_now(),
                "mode": mode,
                "task_id": None,
                "action": "fatal",
                "detail": f"{type(e).__name__}: {e}",
            }
        )
        try:
            _append_ingest_log(events, mode=mode)
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, indent=2))
        return 1
    finally:
        if conn is not None:
            conn.close()


def cmd_rescan(*, task_ids: list[str] | None, dry_run: bool) -> int:
    """Re-validate triples and UPDATE (or insert-if-missing) rows for targets.

    task_ids:
      - non-empty list: only those ids
      - None or empty: all discovered task_ids under qc_evidence
    Marker file is rewritten only if task_ids membership changes.
    """
    events: list[dict[str, Any]] = []
    mode = "rescan_dry_run" if dry_run else "rescan"
    marker = _load_marker()
    ingested_before = set(str(x) for x in (marker.get("task_ids") or []))
    ingested = set(ingested_before)
    groups = _discover_groups()

    if task_ids:
        targets = [str(t) for t in task_ids]
    else:
        targets = sorted(groups.keys())

    summary: dict[str, Any] = {
        "mode": mode,
        "requested_task_ids": list(task_ids) if task_ids else [],
        "targets": targets,
        "discovered_task_ids": sorted(groups.keys()),
        "candidates": [],
        "updated": [],
        "inserted": [],
        "skipped": [],
        "invalid": [],
        "missing": [],
        "marker_saved": False,
    }

    conn: sqlite3.Connection | None = None
    try:
        conn = _connect()
        ensure_task_run_logs_table(conn)

        for task_id in targets:
            if task_id not in groups:
                summary["missing"].append(
                    {"task_id": task_id, "reason": "no_files_in_qc_evidence"}
                )
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "missing",
                        "detail": "no_files_in_qc_evidence",
                    }
                )
                continue

            paths = groups[task_id]
            kinds = sorted(paths.keys())
            entry = {
                "task_id": task_id,
                "kinds": kinds,
                "paths": {k: str(p) for k, p in paths.items()},
            }

            merged, errors = _validate_triple(task_id, paths)
            if errors:
                summary["invalid"].append({**entry, "errors": errors})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "invalid",
                        "detail": errors,
                    }
                )
                continue

            assert merged is not None
            summary["candidates"].append(
                {
                    "task_id": task_id,
                    "final_verdict": merged.get("final_verdict"),
                    "skill_name": merged.get("skill_name"),
                    "paths": merged.get("paths"),
                }
            )

            exists = _row_exists(conn, task_id)

            if dry_run:
                action = "would_update" if exists else "would_insert"
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": action,
                        "detail": {
                            "final_verdict": merged.get("final_verdict"),
                            "skill_name": merged.get("skill_name"),
                        },
                    }
                )
                continue

            try:
                if exists:
                    _update_row(conn, merged)
                    ingested.add(task_id)
                    summary["updated"].append(task_id)
                    events.append(
                        {
                            "ts": _utc_now(),
                            "mode": mode,
                            "task_id": task_id,
                            "action": "update",
                            "detail": {"final_verdict": merged.get("final_verdict")},
                        }
                    )
                else:
                    _insert_row(conn, merged)
                    ingested.add(task_id)
                    summary["inserted"].append(task_id)
                    events.append(
                        {
                            "ts": _utc_now(),
                            "mode": mode,
                            "task_id": task_id,
                            "action": "insert",
                            "detail": {"final_verdict": merged.get("final_verdict")},
                        }
                    )
            except sqlite3.IntegrityError as e:
                summary["skipped"].append({**entry, "reason": f"integrity: {e}"})
                events.append(
                    {
                        "ts": _utc_now(),
                        "mode": mode,
                        "task_id": task_id,
                        "action": "skip",
                        "detail": f"integrity: {e}",
                    }
                )

        if not dry_run and ingested != ingested_before:
            marker["task_ids"] = sorted(ingested)
            _save_marker(marker)
            summary["marker_saved"] = True

        log_path = _append_ingest_log(events, mode=mode)
        summary["ingest_log"] = str(log_path)
        summary["marker_path"] = str(MARKER_PATH)
        summary["dry_run"] = dry_run
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        events.append(
            {
                "ts": _utc_now(),
                "mode": mode,
                "task_id": None,
                "action": "fatal",
                "detail": f"{type(e).__name__}: {e}",
            }
        )
        try:
            _append_ingest_log(events, mode=mode)
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, indent=2))
        return 1
    finally:
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest qc_evidence JSON triples into task_run_logs"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="scan qc_evidence for plan/agent_log/qc_report triples and import new ones",
    )
    parser.add_argument(
        "--rescan",
        nargs="*",
        default=None,
        metavar="TASK_ID",
        help="upsert from triples; optional task_id list (default: all discovered)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --scan or --rescan: show only; no DB write; no marker update",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_marker",
        help="list task_ids in .ingested_tasks.json",
    )
    args = parser.parse_args(argv)

    rescan_set = args.rescan is not None
    if sum(bool(x) for x in (args.scan, args.list_marker, rescan_set)) > 1:
        print(
            "error: --scan, --rescan, and --list are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    if args.dry_run and not args.scan and not rescan_set:
        # allow bare --dry-run as dry scan
        args.scan = True

    if not args.scan and not args.list_marker and not rescan_set:
        parser.print_help()
        return 2

    _ensure_dirs()
    if args.list_marker:
        return cmd_list()
    if rescan_set:
        # [] => all discovered; non-empty => those ids only
        return cmd_rescan(
            task_ids=list(args.rescan) if args.rescan else None,
            dry_run=bool(args.dry_run),
        )
    return cmd_scan(dry_run=bool(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
