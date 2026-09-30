"""computer_presence.py -- the TRIGGER POINT: a computer is present when its
browser opens the LLM Task Monitor page.

THE USER'S DESIGN (2026-09-24, verbatim)
----------------------------------------
    "image is this computer identity"
    "trigger point by http://127.0.0.1:18765/llm-tasks/ open at browser"
    "so you can have status now!"

THE DESIGN, AND WHY IT IS THE RIGHT ONE
---------------------------------------
Opening `http://127.0.0.1:18765/llm-tasks/` IS the trigger. A page load is a
FACT -- a request arrived from that computer -- not an inference. So it is the
honest evidence that the computer is present, and it needs no polling, no
heartbeat process, and no agent running on the machine.

WHY THIS IS NOT `workers`
-------------------------
MEASURED 2026-09-24: `workers.last_seen_at` is the HEARTBEAT worker's column and
`workers.id` is a DIFFERENT id space from `identity_registry.identity_id`.
Joining those two by a bare integer produced a FAKE ON -- the session
`9fc7ad2c-...` showed `ON` only because an unrelated worker happened to also be
id 1. Presence therefore lives in its OWN table, keyed by the COMPUTER, so the
two facts can never be confused again.

THE STATUS IS DERIVED, NEVER DEFAULTED
--------------------------------------
`status_of()` compares the age of `last_seen_at` against a DECLARED threshold:

    ONLINE   age <= ONLINE_WINDOW_SEC
    STALE    age <= STALE_WINDOW_SEC
    OFFLINE  older than that
    UNKNOWN  no row at all

A computer with NO row is `UNKNOWN`, never `OFFLINE`: "we have never seen it" and
"we saw it and it left" are different facts, and collapsing them would make an
unvisited machine look like a machine that went away.

THE TRIGGER MUST NEVER BREAK THE PAGE
-------------------------------------
`touch()` is called from the page route, so it is written to NEVER raise and to
NEVER block: a presence write that can fail a page load would trade a working UI
for a status column. Every failure is swallowed and REPORTED in the return value.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "agent.db"

# THE DECLARED THRESHOLDS. They are module constants, not literals buried in a
# comparison, so a reader can see the policy and a proof can cite it.
ONLINE_WINDOW_SEC = 15 * 60      # seen within 15 min  -> ONLINE
STALE_WINDOW_SEC = 60 * 60       # seen within 60 min  -> STALE, else OFFLINE

STATUSES = ("ONLINE", "STALE", "OFFLINE", "UNKNOWN")


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `computer_presence` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.COMPUTER_PRESENCE_DDL)
    conn.commit()
    return {"ok": True, "table": "computer_presence"}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def touch(conn: sqlite3.Connection, *, computer_id: str, computer_name: str = "",
          ip_address: str = "", user_id: int | None = None, path: str = "",
          cite_ref: str = "", commit: bool = True) -> dict[str, Any]:
    """Record ONE visit from a computer. NEVER raises, NEVER blocks.

    Called from the page route, so a failure here must not fail the page. The
    return value always carries `ok`, and a failure carries `why` -- a swallowed
    error that leaves no trace would be indistinguishable from a success.

    `hits` is INCREMENTED, not replaced, so the row answers both "when was it
    last here" and "how often has it been here".
    """
    cid = str(computer_id or "").strip()
    if not cid:
        return {"ok": False, "why": "computer_id is required (the row's key)"}
    now = _now()
    try:
        row = conn.execute(
            "SELECT presence_id, hits FROM computer_presence WHERE computer_id=?",
            (cid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO computer_presence "
                "(computer_id, computer_name, ip_address, user_id, last_seen_at, "
                " first_seen_at, hits, last_path, cite_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (cid, str(computer_name or ""), str(ip_address or ""), user_id,
                 now, now, str(path or ""), str(cite_ref or "")))
            action = "inserted"
        else:
            conn.execute(
                "UPDATE computer_presence SET computer_name=?, ip_address=?, "
                "user_id=COALESCE(?, user_id), last_seen_at=?, hits=hits+1, "
                "last_path=?, cite_ref=?, updated_at=datetime('now') "
                "WHERE computer_id=?",
                (str(computer_name or ""), str(ip_address or ""), user_id, now,
                 str(path or ""), str(cite_ref or ""), cid))
            action = "updated"
        if commit:
            conn.commit()
        return {"ok": True, "action": action, "computer_id": cid,
                "last_seen_at": now}
    except Exception as exc:
        # A presence write must never break the page it was triggered by.
        return {"ok": False, "why": "%s: %s" % (type(exc).__name__, exc)}


def _age_sec(last_seen_at: str) -> float | None:
    """Seconds since `last_seen_at`, or None when it cannot be parsed.

    None is returned rather than a large number: an unparseable timestamp is
    UNKNOWN, and a large number would silently become OFFLINE.
    """
    t = str(last_seen_at or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(t, fmt)
            return (datetime.datetime.now() - dt).total_seconds()
        except ValueError:
            continue
    return None


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row` on the connection.

    MEASURED DEFECT (2026-09-24): the API endpoint opened its own connection
    WITHOUT a row factory, so `dict(row)` received a TUPLE and raised
    `TypeError: cannot convert dictionary update sequence element #0 to a
    sequence`. The reader must not depend on the CALLER remembering to set it,
    so it sets it here. Idempotent.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def status_of(conn: sqlite3.Connection, computer_id: str) -> dict[str, Any]:
    """The DERIVED status of one computer. Never defaults to a plausible value."""
    _as_rows(conn)
    cid = str(computer_id or "").strip()
    if not cid:
        return {"ok": False, "status": "UNKNOWN",
                "why": "computer_id is required"}
    row = conn.execute(
        "SELECT * FROM computer_presence WHERE computer_id=?", (cid,)).fetchone()
    if row is None:
        return {"ok": True, "computer_id": cid, "status": "UNKNOWN",
                "why": "no computer_presence row: this computer has never "
                       "opened the page, so its presence is NOT measured"}
    age = _age_sec(row["last_seen_at"])
    if age is None:
        return {"ok": True, "computer_id": cid, "status": "UNKNOWN",
                "last_seen_at": row["last_seen_at"],
                "why": "last_seen_at could not be parsed"}
    if age <= ONLINE_WINDOW_SEC:
        status = "ONLINE"
    elif age <= STALE_WINDOW_SEC:
        status = "STALE"
    else:
        status = "OFFLINE"
    return {"ok": True, "computer_id": cid, "status": status,
            "age_sec": round(age, 1), "last_seen_at": row["last_seen_at"],
            "hits": int(row["hits"] or 0),
            "why": "last seen %.0fs ago (ONLINE<=%ds, STALE<=%ds)"
                   % (age, ONLINE_WINDOW_SEC, STALE_WINDOW_SEC)}


def list_presence(conn: sqlite3.Connection, limit: int = 100) -> dict[str, Any]:
    """Every computer, newest first, each with its DERIVED status."""
    _as_rows(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM computer_presence ORDER BY last_seen_at DESC LIMIT ?",
        (max(1, min(int(limit or 100), 500)),))]
    for r in rows:
        st = status_of(conn, str(r["computer_id"]))
        r["status"] = st.get("status", "UNKNOWN")
        r["age_sec"] = st.get("age_sec")
        r["status_why"] = st.get("why", "")
    by: dict[str, int] = {}
    for r in rows:
        by[str(r["status"])] = by.get(str(r["status"]), 0) + 1
    return {"ok": True, "rows": rows, "count": len(rows), "by_status": by,
            "thresholds": {"online_sec": ONLINE_WINDOW_SEC,
                           "stale_sec": STALE_WINDOW_SEC},
            "vocabulary": list(STATUSES)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default="")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        ensure_schema(conn)
        if args.status:
            print(json.dumps(status_of(conn, args.status), indent=2,
                             ensure_ascii=False))
            return 0
        print(json.dumps(list_presence(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
