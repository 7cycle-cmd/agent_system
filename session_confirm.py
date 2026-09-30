# -*- coding: utf-8 -*-
"""session_confirm.py — THE TWO-WAY CONFIRM, with a 6-line record.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "ui can submit request to have chat with vs code > chat > session id to
     confirm!!!! it is 2 way confirm!!!!"
    "evidence at UI for record 6 line with real workable record with detail"
    "non stop untill the 2 way cycle can be proofed with evidence X 3"

THE LOOP, and why ONE leg is not a confirm:

    UI  --submit-->  chat (VS Code / 豆包)      "which session_id are you?"
    chat --reply-->  UI                          the chat echoes its identity
    UI  --compare--> CONFIRM / MISMATCH / PENDING

A one-way request cannot confirm anything: the UI would be asserting an identity
the chat never claimed. The CONFIRM exists only when the chat's OWN answer is
compared against what the UI asked.

THE SIX LINES ARE THE TEMPLATE'S OWN SIX
----------------------------------------
`coord_store.DEFAULT_WORKER_IDENTITY_CONFIRM_INSTRUCTION` already declares
exactly six lines (SESSION_ID / MODEL / TASK_ID / CHAT_ID / CHAT_SHA256 /
CONFIRM). This module does NOT restate them: it PARSES them, and the UI renders
them. Changing the template changes the record with no code change.

THREE OUTCOMES, NO DEFAULT
--------------------------
    CONFIRM   the echoed SESSION_ID equals the one asked
    MISMATCH  it differs (both values are kept, side by side)
    PENDING   no reply yet — a REAL state, never silently treated as CONFIRM

APPEND-ONLY
-----------
A confirmation is a HISTORICAL FACT. A second request must not overwrite the
first row's evidence, or the trace is destroyed. Same rule as `soft_delete_log`.

CLI
---
    python session_confirm.py --request <session_id>
    python session_confirm.py --list
    python session_confirm.py --parse "<reply text>"
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The six fields the confirm template declares. The ORDER is the template's.
CONFIRM_FIELDS: tuple[str, ...] = (
    "SESSION_ID", "MODEL", "TASK_ID", "CHAT_ID", "CHAT_SHA256", "CONFIRM",
)

# A UUID, the shape of a real session id.
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

VERDICTS = ("CONFIRM", "MISMATCH", "PENDING")


class ConfirmRefused(RuntimeError):
    """Raised when a confirm row would be stored without a real session or cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("session_confirm refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `session_confirm_log` and register it in `db_table_registry`."""
    import db_schema as ds

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(ds.SESSION_CONFIRM_LOG_DDL)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("session_confirm_log", "session_confirm_log",
             "the two-way identity confirm, append-only"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "session_confirm_log"}


def parse_echo(text: str) -> dict[str, str]:
    """Extract the SIX fields from a chat's echoed identity block.

    Each field is returned SEPARATELY. A single merged boolean would hide WHICH
    line disagreed, and the user asked for a 6-line record "with detail".

    Two shapes are accepted, because the template is a markdown table and a model
    may answer with either:
        | 1 | SESSION_ID | <uuid> |      (the template's own shape)
        SESSION_ID: <uuid>               (the paste template's shape)
    """
    out: dict[str, str] = {f: "" for f in CONFIRM_FIELDS}
    body = str(text or "")
    for field in CONFIRM_FIELDS:
        # markdown table row: | n | FIELD | value |
        m = re.search(r"\|\s*\d*\s*\|\s*%s\s*\|\s*([^|\n]*)\|" % re.escape(field),
                      body, re.IGNORECASE)
        if not m:
            # "FIELD: value" or "FIELD = value"
            m = re.search(r"%s\s*[:=]\s*([^\n|]*)" % re.escape(field),
                          body, re.IGNORECASE)
        if m:
            out[field] = m.group(1).strip().strip("`*_ ")
    # A SESSION_ID that was not labelled but IS a uuid is still evidence.
    if not out["SESSION_ID"]:
        m = UUID_RE.search(body)
        if m:
            out["SESSION_ID"] = m.group(0)
    return out


def build_lines(asked: dict[str, str], echoed: dict[str, str]) -> list[dict]:
    """The SIX-line record: each field with its ASKED and ECHOED value.

    Line 6 (CONFIRM) is the chat's OWN verdict; the other five are the per-field
    comparison. `match` is computed per line, so a reader sees WHICH line
    disagreed instead of one merged boolean.
    """
    lines: list[dict] = []
    for field in CONFIRM_FIELDS:
        a = str(asked.get(field) or "")
        e = str(echoed.get(field) or "")
        if field == "CONFIRM":
            match = None          # the chat's own verdict, not a comparison
        else:
            match = bool(a) and bool(e) and a.strip() == e.strip()
        lines.append({"field": field, "asked": a, "echoed": e, "match": match})
    return lines


def verdict_for(asked_session: str, echoed: dict[str, str]) -> str:
    """CONFIRM / MISMATCH / PENDING. Never a fourth value, never a default.

    PENDING is returned when there is NO echoed session id at all — a real state
    ("no reply yet"), which must never be silently treated as CONFIRM.
    """
    e = str(echoed.get("SESSION_ID") or "").strip()
    if not e:
        return "PENDING"
    return "CONFIRM" if e == str(asked_session or "").strip() else "MISMATCH"


def request_confirm(conn: sqlite3.Connection, *, session_id: str,
                    identity_key: str = "", asked: dict[str, str] | None = None,
                    cite_ref: str = "") -> dict[str, Any]:
    """Open a confirm request. The row starts PENDING with NO echoed value.

    `chat_id` is NOT accepted: it is the workflow's output. The confirm records
    what the CHAT said, not what the UI hoped.
    """
    reasons: list[str] = []
    sid = str(session_id or "").strip()
    if not sid:
        reasons.append("session_id is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no finding)")
    if reasons:
        raise ConfirmRefused(reasons)

    ensure_schema(conn)
    key = "cf-%s" % uuid.uuid4().hex[:16]
    asked_map = dict(asked or {})
    asked_map.setdefault("SESSION_ID", sid)
    lines = build_lines(asked_map, {})
    conn.execute(
        "INSERT INTO session_confirm_log "
        "(confirm_key, session_id, requested_at, verdict, lines_json, cite_ref) "
        "VALUES (?, ?, datetime('now'), 'PENDING', ?, ?)",
        (key, sid, json.dumps(lines, ensure_ascii=False), str(cite_ref).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM session_confirm_log WHERE confirm_key = ?",
                       (key,)).fetchone()
    return {"ok": True, "created": True, "confirm": dict(row)}


def record_answer(conn: sqlite3.Connection, confirm_key: str, reply_text: str,
                  *, evidence_ref: str = "NA", cite_ref: str = "") -> dict[str, Any]:
    """Record the chat's reply and compute the verdict.

    APPEND-ONLY IN SPIRIT: this fills the ANSWER of an existing request. It never
    rewrites a DIFFERENT request's row, and a second answer to the same request
    is refused, so the first evidence is never overwritten.
    """
    if not str(cite_ref or "").strip():
        raise ConfirmRefused(["cite_ref is required for an answer"])
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM session_confirm_log WHERE confirm_key = ?",
                       (str(confirm_key or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": "no confirm with key %r" % confirm_key}
    if row["answered_at"]:
        return {"ok": False,
                "error": "this confirm is already answered — a confirmation is a "
                         "historical fact and is never overwritten"}

    echoed = parse_echo(reply_text)
    asked = {ln["field"]: ln["asked"]
             for ln in json.loads(row["lines_json"] or "[]")}
    lines = build_lines(asked, echoed)
    verdict = verdict_for(str(row["session_id"]), echoed)
    conn.execute(
        "UPDATE session_confirm_log SET answered_at = datetime('now'), "
        "echoed_session_id = ?, verdict = ?, lines_json = ?, evidence_ref = ?, "
        "evidence_text = ? WHERE confirm_key = ?",
        (echoed.get("SESSION_ID") or None, verdict,
         json.dumps(lines, ensure_ascii=False), str(evidence_ref or "NA"),
         str(reply_text or "")[:4000], str(confirm_key).strip()))
    conn.commit()
    out = conn.execute("SELECT * FROM session_confirm_log WHERE confirm_key = ?",
                       (str(confirm_key).strip(),)).fetchone()
    return {"ok": True, "verdict": verdict, "confirm": dict(out),
            "lines": lines}


def get_confirm(conn: sqlite3.Connection, confirm_key: str) -> dict[str, Any]:
    """One confirm by key, with its six lines decoded."""
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM session_confirm_log WHERE confirm_key = ?",
                       (str(confirm_key or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": "no confirm with key %r" % confirm_key}
    d = dict(row)
    d["lines"] = json.loads(d.get("lines_json") or "[]")
    return {"ok": True, "confirm": d}


def list_confirms(conn: sqlite3.Connection, *,
                  session_id: str = "") -> dict[str, Any]:
    """All confirms, newest first. Optionally for one session."""
    ensure_schema(conn)
    sql = "SELECT * FROM session_confirm_log"
    args: list[Any] = []
    if session_id:
        sql += " WHERE session_id = ?"
        args.append(str(session_id))
    sql += " ORDER BY confirm_id DESC"
    rows = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["lines"] = json.loads(d.get("lines_json") or "[]")
        rows.append(d)
    return {"ok": True, "confirms": rows, "count": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--request", default="")
    ap.add_argument("--answer", nargs=2, metavar=("CONFIRM_KEY", "REPLY_FILE"))
    ap.add_argument("--get", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--parse", default="")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    if args.parse:
        print(json.dumps(parse_echo(args.parse), ensure_ascii=False, indent=2))
        return 0

    conn = _connect(args.db)
    try:
        if args.request:
            print(json.dumps(request_confirm(
                conn, session_id=args.request,
                cite_ref="session_confirm.py:CLI"), ensure_ascii=False, indent=2))
            return 0
        if args.answer:
            text = Path(args.answer[1]).read_text(encoding="utf-8")
            print(json.dumps(record_answer(
                conn, args.answer[0], text,
                cite_ref="session_confirm.py:CLI"), ensure_ascii=False, indent=2))
            return 0
        if args.get:
            print(json.dumps(get_confirm(conn, args.get), ensure_ascii=False,
                             indent=2))
            return 0
        if args.list:
            print(json.dumps(list_confirms(conn), ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
