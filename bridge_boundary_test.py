"""Bridge debounce / write-back boundary tests (no real MCP/Ollama required).

  python bridge_boundary_test.py --yes-wipe
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _init_db(db_path: str) -> None:
    sql = (BASE / "init_db.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def _seed_fault(
    db_path: str,
    evidence_path: str | None,
    evidence_error: str | None = None,
    worker_name: str = "t_worker",
) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO workers (name, status, group_name) VALUES (?, 'offline', 'default')",
            (worker_name,),
        )
        wid = cur.lastrowid
        cur.execute(
            """
            INSERT INTO fault_event
                (worker_id, group_id, fault_type, status, detect_at, evidence_img_path, evidence_error)
            VALUES (?, 'default', 'hang', 'open', ?, ?, ?)
            """,
            (wid, datetime.datetime.now().isoformat(sep=" "), evidence_path, evidence_error),
        )
        eid = cur.lastrowid
        conn.commit()
        return eid
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-wipe", action="store_true", required=True)
    args = parser.parse_args()
    if not args.yes_wipe:
        print("need --yes-wipe")
        return 2

    from openclaw_bridge import has_analysis, process_event
    from vision_analyze import VisionResult

    failures = 0
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _init_db(db_path)

        # fake png
        png = Path(td) / "ev.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        eid = _seed_fault(db_path, str(png))

        fake_vision = VisionResult(
            model="test-model",
            summary="[medium] test ui | cause=unit",
            detail={"severity": "medium", "ui_state": "test"},
            raw_text='{"severity":"medium"}',
            error=None,
        )

        with mock.patch("openclaw_bridge.analyze_evidence", return_value=fake_vision), \
             mock.patch("openclaw_bridge.try_notify", return_value=(True, None)), \
             mock.patch("openclaw_bridge.try_live_snapshot", return_value=(None, "unused")):
            rc = process_event(eid, db_path=db_path)
            if rc != 0:
                print("FAIL first process rc", rc)
                failures += 1
            conn = sqlite3.connect(db_path)
            n = conn.execute("SELECT COUNT(*) FROM fault_analysis WHERE event_id=?", (eid,)).fetchone()[0]
            row = conn.execute(
                "SELECT evidence_used, notified_at, summary FROM fault_analysis WHERE event_id=?",
                (eid,),
            ).fetchone()
            conn.close()
            if n != 1 or row[0] != "frozen" or row[1] is None:
                print("FAIL analysis row", n, row)
                failures += 1
            else:
                print("OK first analysis frozen+notified")

            # debounce second run
            rc2 = process_event(eid, db_path=db_path)
            conn = sqlite3.connect(db_path)
            n2 = conn.execute("SELECT COUNT(*) FROM fault_analysis WHERE event_id=?", (eid,)).fetchone()[0]
            conn.close()
            if rc2 != 0 or n2 != 1:
                print("FAIL debounce", rc2, n2)
                failures += 1
            else:
                print("OK debounce single analysis")

        # missing evidence → live path
        eid2 = _seed_fault(db_path, None, "EVIDENCE_MISSING: test", worker_name="t_worker_2")
        live_png = Path(td) / "live.png"
        live_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 32)
        with mock.patch("openclaw_bridge.analyze_evidence", return_value=fake_vision), \
             mock.patch("openclaw_bridge.try_notify", return_value=(False, "mcp_notify:skip")), \
             mock.patch("openclaw_bridge.try_live_snapshot", return_value=(str(live_png), None)):
            rc3 = process_event(eid2, db_path=db_path)
            conn = sqlite3.connect(db_path)
            row2 = conn.execute(
                "SELECT evidence_used, evidence_path, error FROM fault_analysis WHERE event_id=?",
                (eid2,),
            ).fetchone()
            conn.close()
            if rc3 != 0 or not row2 or row2[0] != "live":
                print("FAIL live fallback", rc3, row2)
                failures += 1
            else:
                print("OK live evidence fallback", row2)

        # has_analysis helper
        conn = sqlite3.connect(db_path)
        from db_schema import ensure_fault_analysis_schema
        ensure_fault_analysis_schema(conn)
        assert has_analysis(conn, eid) is True
        assert has_analysis(conn, 999999) is False
        conn.close()
        print("OK has_analysis helper")

    if failures:
        print(f"FAILED {failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
