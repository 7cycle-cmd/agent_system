# -*- coding: utf-8 -*-
"""worker_help.py — a worker asks for help: log it, then notify via OpenClaw.

Every step is DB-recorded, so the request is auditable and measurable:

  1. append_lifecycle_event(event_type='state_update', comment='need help: ...')
     -> task_lifecycle_log row (task_id + event_sequence + task_state + actor)
  2. openclaw_bridge.try_notify(title, body)
     -> OpenClaw MCP system.notify (the human gets pinged)

The DB write happens FIRST so the audit trail exists even if the notify fails.
Notify never blocks the trunk (try_notify swallows its own errors).

Usage:
  python worker_help.py --task-id 109 --message "ComfyUI torch install failed"
  python worker_help.py --task-id 109 --message "..." --no-notify

Exit codes: 0 logged (notify optional), 1 bad args, 2 lifecycle write failed.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def ask_help(task_id: str, message: str, notify: bool = True) -> dict:
    """Log the help request, then notify. Returns a result dict (never raises)."""
    out: dict = {
        "ok": False,
        "lifecycle_id": None,
        "event_sequence": None,
        "notified": False,
        "error": None,
    }

    # 1) DB record FIRST — the audit trail must exist even if notify fails.
    try:
        from src.task_center.lifecycle_log import append_lifecycle_event

        res = append_lifecycle_event({
            "task_id": task_id,
            "event_type": "state_update",
            "task_state": "validating",
            "actor": "worker",
            "comment": f"need help: {message}",
            "event_summary": message[:200],
        })
        # The id lives under `row` (append_lifecycle_event returns the full
        # validation envelope: ok / result / row / NEW_LIFECYCLE_LOG_ROW).
        row = (res or {}).get("row") or (res or {}).get("NEW_LIFECYCLE_LOG_ROW") or {}
        out["lifecycle_id"] = row.get("lifecycle_id")
        out["event_sequence"] = row.get("event_sequence")
        if not (res or {}).get("ok"):
            out["error"] = "lifecycle: " + "; ".join(
                (res or {}).get("errors") or ["unknown"]
            )
    except Exception as e:
        out["error"] = f"lifecycle: {type(e).__name__}: {e}"

    # 2) Notify (optional; never blocks the trunk).
    if notify:
        try:
            from openclaw_bridge import try_notify

            ok, err = try_notify(f"Worker needs help [{task_id}]", message)
            out["notified"] = bool(ok)
            if not ok:
                out["error"] = (out["error"] or "") + f" notify:{err}"
        except Exception as e:
            out["error"] = (out["error"] or "") + f" notify:{type(e).__name__}: {e}"

    out["ok"] = bool(out["lifecycle_id"])
    return out


def main() -> None:
    if "--task-id" not in sys.argv or "--message" not in sys.argv:
        print("usage: worker_help.py --task-id ID --message TEXT [--no-notify]")
        sys.exit(1)
    tid = sys.argv[sys.argv.index("--task-id") + 1]
    msg = sys.argv[sys.argv.index("--message") + 1]
    notify = "--no-notify" not in sys.argv
    r = ask_help(tid, msg, notify=notify)
    print(
        "lifecycle_id=%s seq=%s notified=%s ok=%s err=%s"
        % (r["lifecycle_id"], r["event_sequence"], r["notified"], r["ok"], r["error"])
    )
    sys.exit(0 if r["ok"] else 2)


if __name__ == "__main__":
    main()
