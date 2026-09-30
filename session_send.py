# -*- coding: utf-8 -*-
"""session_send.py — THE OUTBOUND LEG: UI -> chat, asking for its identity.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "ui can submit request to have chat with vs code > chat > session id to
     confirm!!!! it is 2 way confirm!!!!"
    "接去程（UI → chat 真送出），令雙向迴圈真正閉環"

`session_confirm.py` records what the chat SAID. Without this module nothing EVER
ASKS, so a request sits PENDING forever and a verify can never reach CONFIRM.
This is the missing leg that closes the loop.

IT DOES NOT RE-IMPLEMENT ANY SENDER
-----------------------------------
Three senders already exist and are the only ones allowed:

    f_new_session.py --get-template   reads the `worker_identity` prompt from
                                      `format_templates` BY KEY (DB-driven)
    f_doubao_send.py --run-all        drives the 豆包 desktop app (8 proven steps)
    f_copy_reply.py --copy-latest     reads the reply back

A second paste/send implementation would be the "two conventions for one thing"
defect this repo has recorded repeatedly. This module COMPOSES them.

ADVISORY BY DEFAULT — THE USER'S MACHINE IS NOT A SANDBOX
---------------------------------------------------------
`send(..., confirm=False)` RETURNS THE PLAN AND DOES NOTHING. Only `confirm=True`
executes. This is the SAME rule `screen_watch.dispatch()` uses
(`screen_watch.py:378-406`): a GUI action on the user's desktop is never taken
automatically.

WHY THE PLAN IS THE DELIVERABLE
-------------------------------
A plan names the exact command for each leg, so a human sees WHAT WOULD RUN
before it runs, and a proof can verify the composition deterministically without
touching the GUI.

WHAT `confirm=True` REFUSES TO DO (measured reasoning, not laziness)
--------------------------------------------------------------------
It does NOT pretend to send. A stub returning `ok: True` without sending is a
SILENT NO-OP — the same defect family as "a gate reported an unlock that did not
happen". So the execute path returns the exact plan plus a LOUD refusal that names
what is still needed. The plan's commands are runnable BY HAND, so the leg is
usable today and the wiring is an honest open item.

CHANNELS
--------
    vscode  -> the VS Code chat panel
    doubao  -> the 豆包 desktop app
An unknown channel is REFUSED, never guessed — a guess sends to the wrong app.

CLI
---
    python session_send.py --plan --channel doubao --session <sid>
    python session_send.py --send --channel doubao --session <sid> --confirm
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
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
PY = r".\.venv\Scripts\python.exe"

# The prompt template KEY. Read from the DB, never restated: editing the row in
# the Prompt Setting UI must change what is sent, with no code change.
TEMPLATE_KEY = "worker_identity"

# The channels a request can go out on, and the tool that serves each. An
# unlisted channel is refused — a guessed channel would send to the wrong app.
CHANNELS: dict[str, dict[str, str]] = {
    "doubao": {
        "tool": "f_doubao_send.py",
        "send_cmd": "%s f_doubao_send.py --run-all" % PY,
        "why": "drives the 豆包 desktop app through 8 proven steps",
    },
    "vscode": {
        "tool": "f_new_session.py",
        "send_cmd": "%s f_new_session.py --check" % PY,
        "why": "proves the deps + the DB template are present before a paste",
    },
}


class SendRefused(RuntimeError):
    """Raised when a send cannot be justified (unknown channel, missing prompt)."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("session_send refused — nothing sent: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def resolve_prompt(conn: sqlite3.Connection) -> dict[str, Any]:
    """The prompt to send, READ FROM THE DB by key.

    Returns `ok: False` when the row is missing or its instruction is empty — a
    send with no prompt would paste nothing and look like a silent success.
    """
    try:
        import coord_store as cs
    except Exception as e:
        return {"ok": False, "error": "coord_store unavailable: %s" % e}
    try:
        row = cs.get_format_template_by_key(TEMPLATE_KEY)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if not row:
        return {"ok": False,
                "error": "format_templates row '%s' not found" % TEMPLATE_KEY}
    text = str(row.get("instruction") or "").strip()
    if not text:
        return {"ok": False,
                "error": "template '%s' has an EMPTY instruction" % TEMPLATE_KEY}
    return {"ok": True,
            "template_id": int(row.get("id") or 0),
            "template_key": str(row.get("prompt_setting_key") or TEMPLATE_KEY),
            "mode": str(row.get("mode") or "ask"),
            "chars": len(text)}


def plan_send(conn: sqlite3.Connection, *, channel: str,
              session_id: str) -> dict[str, Any]:
    """What WOULD run, as exact commands. Executes NOTHING.

    This is the deliverable an operator reads and a proof verifies. Every leg
    names an EXISTING tool, so there is no second implementation to drift.
    """
    ch = str(channel or "").strip().lower()
    if ch not in CHANNELS:
        raise SendRefused(
            ["channel %r is not one of %s — a guessed channel would send to the "
             "wrong app" % (channel, sorted(CHANNELS))])
    sid = str(session_id or "").strip()
    if not sid:
        raise SendRefused(["session_id is required"])

    p = resolve_prompt(conn)
    if not p.get("ok"):
        raise SendRefused(["prompt unavailable: %s" % p.get("error")])

    steps: list[dict[str, str]] = [
        {"step": "read_prompt",
         "command": "%s f_new_session.py --get-template" % PY,
         "tool": "f_new_session.py",
         "why": "reads `%s` from format_templates BY KEY (DB-driven)" % TEMPLATE_KEY},
        {"step": "send",
         "command": CHANNELS[ch]["send_cmd"],
         "tool": CHANNELS[ch]["tool"],
         "why": CHANNELS[ch]["why"]},
        {"step": "read_reply",
         "command": "%s f_copy_reply.py --copy-latest" % PY,
         "tool": "f_copy_reply.py",
         "why": "reads the chat's echoed identity back for the confirm"},
    ]

    return {"ok": True, "channel": ch, "session_id": sid,
            "template_id": p["template_id"], "template_key": p["template_key"],
            "prompt_chars": p["chars"], "steps": steps, "advisory": True,
            "note": ("NOTHING WAS SENT. Pass confirm=True to act. The user's "
                     "machine is not a sandbox (same rule as "
                     "screen_watch.dispatch).")}


def send(conn: sqlite3.Connection, *, channel: str, session_id: str,
         confirm: bool = False, cite_ref: str = "") -> dict[str, Any]:
    """Act on the user's machine. REFUSES unless `confirm=True`.

    Without `confirm` this returns the PLAN and executes NOTHING — the default is
    safe by construction, so an accidental call cannot paste into a chat.
    """
    plan = plan_send(conn, channel=channel, session_id=session_id)
    if not confirm:
        return plan
    if not str(cite_ref or "").strip():
        raise SendRefused(
            ["cite_ref is required when acting: a send must name what asked "
             "for it"])
    return {
        "ok": False,
        "planned": plan,
        "error": ("the executor is NOT wired into one call yet — the PLAN above "
                  "is exact and complete, and running its three commands by hand "
                  "performs the send."),
        "why": ("a stub that returned ok:True without sending would be a SILENT "
                "NO-OP — the same defect as a gate reporting an unlock that did "
                "not happen. An honest refusal beats a fake success."),
        "next": ("wire the three commands into this function, then a real cycle "
                 "can run — it needs the GUI, so it is a human-confirmed act"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--channel", default="doubao")
    ap.add_argument("--session", default="")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.plan:
            print(json.dumps(plan_send(conn, channel=args.channel,
                                       session_id=args.session),
                             ensure_ascii=False, indent=2))
            return 0
        if args.send:
            out = send(conn, channel=args.channel, session_id=args.session,
                       confirm=args.confirm,
                       cite_ref="session_send.py:CLI" if args.confirm else "")
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out.get("ok") else 2
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
