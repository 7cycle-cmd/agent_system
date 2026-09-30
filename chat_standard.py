# -*- coding: utf-8 -*-
"""chat_standard.py — the `chat` standard: MEASURE it, EARN it, then GATE on it.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "what is that?"
    "chat title and chat content is where?"
    "ｗｈｅｒ　ｉｓ　ｔａｓｋ　ＩＤ　ａｎｄ　ｅｎｔｉｔｙ　ＩＤtoo?"
    "how to be table standardize by evidecne +ｌｏｇｉｃ　ｇｅｎｅｒａｔｏｒ"
    "C → A → B gogogo"

C MEASURED, and this is the answer: the standard for `chat` ALREADY EXISTS and is
COMPLETE — `pattern_template` template_id **18** carries the rule, the why and a
copyable example; `dimension_binding_registry` carries **all 6 dimensions,
active**; and `logic_generator` reads it (8 fields, 6 dimensions, 12 questions).
**It is `is_active=0`, so the gate REFUSES it** — correctly, because
`pattern_template.assert_conforms` says *"an inactive template is a CLAIM, not an
accepted rule"*.

A is this module: RECORD the evidence, then ACTIVATE through the ONE writer.

B is the two columns the standard has no home for: `chat.task_id` and
`chat.entity_id`. MEASURED: `chat` has 8 columns and NEITHER. The task id exists
only as TEXT inside `chat.title` / `chat_key` (`chat:report:<task_id>`).

THE THREE THINGS THIS MODULE REFUSES TO DO
------------------------------------------
1. **It does not write `pattern_template.is_active`.** `activate_template`
   (`pattern_template.py:869`) is the ONE writer, and it REFUSES without a PASS
   instance. This module DELEGATES.
2. **It does not write `pattern_instance`.** `record_instance`
   (`pattern_template.py:688`) is the ONE writer. This module DELEGATES.
3. **It does not invent an `entity_id`.** The human's own rule is *"entity ID
   (optional)"*, so a chat that names no entity is a VALID chat and its
   `entity_id` stays NULL.

THE EVIDENCE IS A MEASUREMENT, NOT A FIXTURE
--------------------------------------------
The rule is *"the chat is a row in `chat_main` keyed on its session"*. So the
check is: does a real chat row resolve to a `chat_main` row with a non-empty
`session_id`? MEASURED: **86 of 329** do. Each is a PASS instance; each that does
not is a FAIL instance. Both populations are NAMED, never summed into one number.

Run:
    .\\.venv\\Scripts\\python.exe chat_standard.py --measure
    .\\.venv\\Scripts\\python.exe chat_standard.py --record --apply
    .\\.venv\\Scripts\\python.exe chat_standard.py --activate --apply
    .\\.venv\\Scripts\\python.exe chat_standard.py --backfill --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pattern_template as pt  # noqa: E402  — the ONE writer of the standard

DB = BASE / "agent.db"

# The subject kind and the item kind this module owns. READ from the standard,
# never invented: MEASURED, `pattern_template` template_id 18 is
# `subject_kind='chat'`, `item_kind='registered'`.
SUBJECT_KIND = "chat"
ITEM_KIND = "registered"

# The rule, quoted from the template row so a reader can check it without a query.
RULE = "the chat is a row in chat_main keyed on its session"

# The task id inside a report chat's key: `chat:report:<task_id>`.
REPORT_KEY_RE = re.compile(r"^chat:report:(.+)$")


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which chats satisfy the rule, and which do not. BOTH populations named.

    THE RULE IS THE TEMPLATE'S OWN, not a new one: *"the chat is a row in
    `chat_main` keyed on its session"*. A chat CONFORMS when it resolves to a
    `chat_main` row whose `session_id` is non-empty. A chat that resolves to
    nothing, or to a row with no session, does NOT — and that is a FAIL, not a
    silent skip.
    """
    rows = [dict(r) for r in conn.execute(
        """
        SELECT ch.chat_id, ch.chat_key, ch.title,
               (SELECT cm.session_id FROM chat_main cm
                 WHERE cm.chat_id = ch.chat_id
                 ORDER BY cm.id LIMIT 1) AS session_id
          FROM chat ch
         ORDER BY ch.chat_id
        """)]
    conforming = [r for r in rows
                  if str(r.get("session_id") or "").strip()]
    failing = [r for r in rows if not str(r.get("session_id") or "").strip()]
    return {
        "ok": True,
        "subject_kind": SUBJECT_KIND,
        "item_kind": ITEM_KIND,
        "rule": RULE,
        "chats": len(rows),
        "conforming": len(conforming),
        "failing": len(failing),
        "conforming_ids": [int(r["chat_id"]) for r in conforming],
        "failing_ids": [int(r["chat_id"]) for r in failing],
        "why": ("%d of %d chats resolve to a chat_main row with a session; %d do "
                "not, and each of those is a FAIL instance of the rule"
                % (len(conforming), len(rows), len(failing))),
    }


def record(conn: sqlite3.Connection, *, apply: bool = False,
           limit: int | None = None) -> dict[str, Any]:
    """Write ONE `pattern_instance` per measured chat, through the ONE writer.

    A PASS for a conforming chat, a FAIL for one that does not. `subject_ref` is
    the chat's own key, so the instance names WHICH chat it measured, and
    `cite_ref` is the command that produced the verdict.
    """
    m = measure(conn)
    todo: list[tuple[int, str, str]] = []
    for cid in m["conforming_ids"]:
        todo.append((cid, "PASS", "chat:%d" % cid))
    for cid in m["failing_ids"]:
        todo.append((cid, "FAIL", "chat:%d" % cid))
    if limit is not None:
        todo = todo[:int(limit)]
    if not apply:
        return {"ok": True, "applied": False, "would_record": len(todo),
                "pass": len(m["conforming_ids"]),
                "fail": len(m["failing_ids"]),
                "why": "report-only; pass --apply to write"}
    written = {"PASS": 0, "FAIL": 0}
    for cid, verdict, ref in todo:
        res = pt.record_instance(
            conn, subject_kind=SUBJECT_KIND, subject_ref=ref, verdict=verdict,
            item_kind=ITEM_KIND,
            # THE TEMPLATE'S OWN KEY IS NULL, and the INSTANCE's is `chat:<id>`.
            # MEASURED, and this was a real defect: passing the instance's ref as
            # the template lookup key found NO template, so all 329 instances
            # were written with `template_id=NULL` and the PASS evidence could
            # never accrue. The two meanings are now separate parameters.
            template_subject_ref=None,
            detail=("chat %d: %s" % (cid, RULE)),
            cite_ref="chat_standard.py:measure")
        if res.get("ok"):
            written[verdict] += 1
    return {"ok": True, "applied": True, "recorded": sum(written.values()),
            "pass": written["PASS"], "fail": written["FAIL"],
            "why": ("recorded %d PASS and %d FAIL instance(s) of %r"
                    % (written["PASS"], written["FAIL"], RULE))}


def evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    """The activation evidence, READ from the ONE implementation."""
    return pt.activation_evidence(conn, subject_kind=SUBJECT_KIND,
                                  item_kind=ITEM_KIND)


def activate(conn: sqlite3.Connection, *, apply: bool = False,
             minimum: int = 1) -> dict[str, Any]:
    """Activate the template, through the ONE writer, gated on PASS evidence.

    `activate_template` REFUSES when there is no PASS instance, so this function
    cannot activate an unproven rule even if a caller asks it to.
    """
    ev = evidence(conn)
    if not apply:
        return {"ok": True, "applied": False, "passing": ev.get("passing"),
                "is_active": ev.get("is_active"), "minimum": int(minimum),
                "would_activate": int(ev.get("passing") or 0) >= int(minimum),
                "why": "report-only; pass --apply to write"}
    res = pt.activate_template(conn, subject_kind=SUBJECT_KIND,
                               item_kind=ITEM_KIND, minimum=int(minimum),
                               cite_ref="chat_standard.py:activate")
    return res


def conform(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    """Run the gate for ONE chat and report the verdict.

    `present` is the MEASUREMENT, not an argument: a chat conforms when it
    resolves to a `chat_main` row with a session. So a caller cannot pass
    `present=True` for a chat that does not.
    """
    row = conn.execute(
        "SELECT ch.chat_id, ch.chat_key, "
        "       (SELECT cm.session_id FROM chat_main cm "
        "         WHERE cm.chat_id = ch.chat_id ORDER BY cm.id LIMIT 1) AS session_id "
        "  FROM chat ch WHERE ch.chat_id = ?", (int(chat_id),)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_CHAT",
                "message": "no chat with chat_id=%d" % int(chat_id)}
    present = bool(str(row["session_id"] or "").strip())
    res = pt.assert_conforms(conn, subject_kind=SUBJECT_KIND, item_kind=ITEM_KIND,
                             present=present,
                             observed=("chat %d session_id=%r"
                                       % (int(chat_id), row["session_id"])))
    res["chat_id"] = int(chat_id)
    res["present"] = present
    return res


# ---- B: the two columns the standard has no home for ------------------------

def task_id_from_key(chat_key: str) -> str:
    """The task id a report chat's key already carries. DERIVED, never invented.

    MEASURED: a report chat's key is `chat:report:<task_id>`, so the task id is
    ALREADY in the row — it was only ever readable by parsing a string. A key
    that is not a report key has NO task id, and `''` is returned rather than a
    guess.
    """
    m = REPORT_KEY_RE.match(str(chat_key or "").strip())
    return m.group(1) if m else ""


def backfill(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Fill `chat.task_id` from `chat_key`. IDEMPOTENT. `entity_id` stays NULL.

    THE TASK ID IS DERIVED. THE ENTITY ID IS NOT. The human's own rule is
    *"entity ID (optional)"*, so a chat that names no entity is a VALID chat;
    inventing one would be the defect this repo keeps paying for.
    """
    # THE COLUMNS ARE ADDED BY THE ONE SCHEMA WRITER, not by this module.
    # MEASURED: `chat` had 8 columns and no `task_id`, and `CREATE TABLE IF NOT
    # EXISTS` never adds one to an existing table — so `ensure_schema` is what
    # makes the column exist, and this module only FILLS it.
    import chat_level as cl
    cl.ensure_schema(conn)
    cols = _columns(conn, "chat")
    if "task_id" not in cols:
        return {"ok": False, "code": "NO_TASK_ID_COLUMN",
                "message": ("chat has no `task_id` column; run "
                            "chat_level.ensure_schema first (columns: %s)"
                            % ", ".join(cols))}
    rows = [dict(r) for r in conn.execute(
        "SELECT chat_id, chat_key, task_id FROM chat ORDER BY chat_id")]
    todo = [r for r in rows
            if not str(r.get("task_id") or "").strip()
            and task_id_from_key(r["chat_key"])]
    if not apply:
        return {"ok": True, "applied": False, "chats": len(rows),
                "would_fill": len(todo),
                "already": len(rows) - len(todo),
                "why": "report-only; pass --apply to write"}
    filled = 0
    for r in todo:
        tid = task_id_from_key(r["chat_key"])
        conn.execute("UPDATE chat SET task_id=?, updated_at=datetime('now') "
                     "WHERE chat_id=?", (tid, int(r["chat_id"])))
        filled += 1
    conn.commit()
    return {"ok": True, "applied": True, "chats": len(rows), "filled": filled,
            "already": len(rows) - len(todo),
            "entity_id_filled": 0,
            "why": ("filled %d task_id(s) DERIVED from chat_key; entity_id is "
                    "left NULL because a chat with no entity is valid"
                    % filled)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--conform", type=int, metavar="CHAT_ID")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    conn = _connect()
    try:
        if args.measure:
            m = measure(conn)
            print("chats=%d conforming=%d failing=%d"
                  % (m["chats"], m["conforming"], m["failing"]))
            print("rule: %s" % m["rule"])
            print(m["why"])
            return 0
        if args.conform is not None:
            r = conform(conn, args.conform)
            print("%s chat=%s present=%s" % (r.get("code"), r.get("chat_id"),
                                             r.get("present")))
            if r.get("message"):
                print(r["message"])
            return 0 if r.get("ok") else 1
        if args.record:
            r = record(conn, apply=args.apply)
            print("recorded=%s pass=%s fail=%s"
                  % (r.get("recorded", r.get("would_record")), r.get("pass"),
                     r.get("fail")))
            print(r["why"])
            return 0
        if args.activate:
            r = activate(conn, apply=args.apply)
            print("%s passing=%s is_active=%s"
                  % (r.get("code"), r.get("passing"), r.get("is_active")))
            if r.get("message"):
                print(r["message"])
            return 0 if r.get("ok") else 1
        if args.backfill:
            r = backfill(conn, apply=args.apply)
            print("chats=%s filled=%s already=%s entity_id_filled=%s"
                  % (r.get("chats"), r.get("filled", r.get("would_fill")),
                     r.get("already"), r.get("entity_id_filled", 0)))
            print(r.get("why") or r.get("message"))
            return 0 if r.get("ok") else 1
        ap.error("one of --measure / --record / --activate / --backfill / "
                 "--conform is required")
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
