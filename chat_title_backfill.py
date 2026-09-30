# -*- coding: utf-8 -*-
"""chat_title_backfill.py — give the 69 existing chats a title, FROM EVIDENCE.

THE HUMAN (2026-09-27):
    "**未結項（honest）：** 69 個現有 chat 冇 title（REPORTED）... do it now"

`CHAT.TITLE.CONTENT.ENTITY` made `title` REQUIRED at the write site, but the 69
rows written BEFORE the rule still have no title. This module backfills them.

THE TITLE IS DERIVED, NEVER INVENTED
------------------------------------
MEASURED (this session): of the 69 chats, the first `role='Question'` content is
the placeholder `'Q1'` for **48**, real content for **18**, and absent for **3**.
So a title CANNOT be derived from content for 48 of them.

A chat whose content is a placeholder gets a title that SAYS SO
(`"<chat_key> (no question content)"`), never an invented one. An invented title
would be a name nobody can check, which is the defect `terminology-register`
forbids ("no name without a registered term").

WHAT IT REFUSES
---------------
  * inventing a title that is not derived from evidence;
  * re-titling a chat that already has one (a title change is a separate act).

    .\\.venv\\Scripts\\python.exe chat_title_backfill.py --measure
    .\\.venv\\Scripts\\python.exe chat_title_backfill.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The placeholder content a chat may carry. MEASURED: 48 of 69 chats have this
# as their first Question, so it is NOT a title — it is the ABSENCE of one.
PLACEHOLDER_CONTENTS = ("q1", "q", "question", "test", "na", "n/a", "-", ".")
# A title is one line, and a reader must be able to scan it.
MAX_TITLE_CHARS = 80


def _first_question(conn: sqlite3.Connection, session_id: str) -> str:
    """The first `role='Question'` content for a session, or `''`."""
    row = conn.execute(
        "SELECT content FROM chat_center_message WHERE session_id=? AND "
        "role='Question' ORDER BY id LIMIT 1", (session_id,)).fetchone()
    return str(row["content"] or "") if row else ""


def derive_title(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    """The title for a chat, DERIVED from evidence. Returns `{title, source}`.

    The source is NAMED, so a reader can see WHERE the title came from:
      * `question_content` — the chat's own first Question, trimmed to one line;
      * `no_question_content` — the content is a placeholder or absent, so the
        title SAYS the content is missing (never an invented name).
    """
    ch = conn.execute("SELECT chat_id, chat_key, title FROM chat WHERE chat_id=?",
                      (int(chat_id),)).fetchone()
    if not ch:
        return {"ok": False, "code": "NO_SUCH_CHAT", "chat_id": int(chat_id)}
    key = str(ch["chat_key"])
    # the session id is the chat_key without the `chat:` prefix
    sid = key[5:] if key.startswith("chat:") else key
    content = _first_question(conn, sid)
    # one line, collapsed whitespace
    line = " ".join(content.split())
    if line and line.lower() not in PLACEHOLDER_CONTENTS:
        title = line[:MAX_TITLE_CHARS]
        return {"ok": True, "chat_id": int(chat_id), "chat_key": key,
                "title": title, "source": "question_content",
                "evidence": "chat_center_message first Question for session %s"
                            % sid[:12]}
    # NO REAL CONTENT: the title SAYS SO. It is not an invented name.
    title = "%s (no question content)" % key
    return {"ok": True, "chat_id": int(chat_id), "chat_key": key,
            "title": title[:MAX_TITLE_CHARS + 40],
            "source": "no_question_content",
            "evidence": ("first Question content is %r (a placeholder or "
                         "absent), so the title states the content is missing"
                         % (content[:20] if content else ""))}


def backfill(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Give every chat with no title a DERIVED one. Idempotent.

    A chat that already has a title is SKIPPED, so a second run writes 0 rows.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT chat_id, chat_key, title FROM chat ORDER BY chat_id")]
    todo = [r for r in rows
            if not str(r["title"] or "").strip()]
    derived: list[dict[str, Any]] = []
    written = 0
    for r in todo:
        d = derive_title(conn, int(r["chat_id"]))
        derived.append(d)
        if apply and d.get("ok"):
            conn.execute("UPDATE chat SET title=?, updated_at=datetime('now') "
                         "WHERE chat_id=?", (d["title"], int(r["chat_id"])))
            written += 1
    if apply:
        conn.commit()
    by_source: dict[str, int] = {}
    for d in derived:
        by_source[d.get("source", "?")] = by_source.get(d.get("source", "?"), 0) + 1
    return {"ok": True, "applied": bool(apply),
            "chats_total": len(rows), "already_titled": len(rows) - len(todo),
            "to_title": len(todo), "written": written,
            "by_source": by_source, "derived": derived,
            "cite": ("measured: chat.title, chat_center_message first Question "
                     "per session")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true",
                    help="report the derived titles, write nothing")
    ap.add_argument("--apply", action="store_true",
                    help="write the derived titles")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        if args.measure or args.apply:
            res = backfill(conn, apply=bool(args.apply))
            print("CHAT TITLE BACKFILL  applied=%s" % res["applied"])
            print("  chats_total    : %d" % res["chats_total"])
            print("  already_titled : %d" % res["already_titled"])
            print("  to_title       : %d" % res["to_title"])
            print("  written        : %d" % res["written"])
            print("  by_source      : %s" % res["by_source"])
            for d in res["derived"][:10]:
                print("    chat_id=%-4s %-14s %r"
                      % (d.get("chat_id"), d.get("source"), d.get("title")))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
