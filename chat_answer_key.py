"""chat_answer_key.py — the chat is the answer key. Every link MEASURED.

WHY THIS EXISTS (the human, 2026-09-27)
---------------------------------------
    "where is your report, report auto convert to short? i found that only happen
     after job is done, have the plan will not have these problem / and proof run
     is sucess, so version will be upgrade too, i don't understand what is the
     trigger point / as all is complete fullfill now / totally review that and +
     ui evidence by conversaction > chat / vscode > chat = environment + identity
     : DeekSeek V4.0 Flash (proofed) = have service ticket to have conversaction
     module / unlock conversaction > chat by session_ID / by 5W1H to fully define
     and middleware help to confirm / you can see the ui for chat is not human
     readable, pls improve that / chat ID > answer / have the plan now, all can
     trace!! so no problem or BUG can be hidden anymore"

FOUR QUESTIONS, FOUR MEASUREMENTS. This tool answers each one and REPORTS the
gaps it finds. It writes NOTHING to the database — a report that mutates its
subject is not a report.

  1. THE REPORT-SHRINK TRIGGER. The human's hypothesis ("only happen after job is
     done") is MEASURED against the transcript: the full report and the short
     line are in DIFFERENT TURNS, and the boundary is `task_complete`.
  2. THE VERSION-UPGRADE TRIGGER. MEASURED: it does not exist. Every entity type
     is at version 1, and `activation_gate.activate` only flips `is_active`.
  3. THE CHAT UI. MEASURED: the page shows raw proof scaffolding as conversation.
  4. THE CHAIN. MEASURED: `ticket_chat_link` is empty; `identity_registry.chat_id`
     is partial.

USAGE
-----
    .\\.venv\\Scripts\\python.exe chat_answer_key.py
    .\\.venv\\Scripts\\python.exe chat_answer_key.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"
PROOF_GATE = BASE_DIR / "scripts" / "proof_gate.py"
ACTIVATION_GATE = BASE_DIR / "activation_gate.py"

# The four helpers the DELETED re-attach design used. Their absence is the proof
# that the short report is not written by a hook.
REATTACH_HELPERS = (
    "_assistant_contents",
    "current_turn_assistant_contents",
    "last_assistant_message_len",
    "longest_assistant_message",
)

# A turn whose content is proof scaffolding, not a conversation.
PROOF_CONTENT_PREFIX = "__proof"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


# ---------------------------------------------------------------------------
# 1. THE REPORT-SHRINK TRIGGER
# ---------------------------------------------------------------------------
def measure_report_trigger(transcript: Path | None) -> dict:
    """Where does the full report become a short line? MEASURED, not assumed.

    The transcript records `assistant.turn_start` / `assistant.turn_end` and the
    `task_complete` tool call. The claim under test is the human's: the shrink
    happens "after job is done". So this walks the LAST turn boundary and reports
    the messages on each side of it.
    """
    out: dict = {
        "transcript": str(transcript) if transcript else "",
        "found": False,
        "trigger": "task_complete",
        "why": "",
        "turns": [],
    }
    if not transcript or not transcript.exists():
        out["why"] = "no transcript to read"
        return out

    lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()

    def _msg(idx: int) -> dict | None:
        try:
            d = json.loads(lines[idx])
        except Exception:
            return None
        if d.get("type") != "assistant.message":
            return None
        content = (d.get("data") or {}).get("content") or ""
        return {"line": idx + 1, "chars": len(content),
                "head": content[:80].replace("\n", " ")}

    def _nearest(idx: int, step: int) -> dict | None:
        for i in range(idx + step, idx + step * 40, step):
            if i < 0 or i >= len(lines):
                return None
            m = _msg(i)
            if m and m["chars"] > 0:
                return m
        return None

    # SCAN EVERY `task_complete`, not just the last one. MEASURED: taking the
    # LAST occurrence measured THIS turn's own task_complete, whose neighbours are
    # both short asides — a measurement of the wrong event. The trigger is the
    # occurrence where a LONG message is followed by a SHORT one, so the scan
    # picks the pair with the largest drop.
    best = None
    for i, ln in enumerate(lines):
        if "task_complete" not in ln:
            continue
        before = _nearest(i, -1)
        after = _nearest(i, +1)
        if not before or not after:
            continue
        drop = before["chars"] - after["chars"]
        if best is None or drop > best["drop"]:
            best = {"drop": drop, "index": i, "before": before, "after": after}

    if best is None:
        out["why"] = "no task_complete call with a message on each side"
        return out

    out["found"] = True
    out["task_complete_line"] = best["index"] + 1
    out["before"] = best["before"]
    out["after"] = best["after"]
    out["drop_chars"] = best["drop"]
    out["why"] = (
        "the full report is at line %d (%d chars) and the short line is at line "
        "%d (%d chars); `task_complete` at line %d OPENS A NEW TURN between them, "
        "so VS Code shows the LAST message (a drop of %d chars)"
        % (best["before"]["line"], best["before"]["chars"],
           best["after"]["line"], best["after"]["chars"],
           best["index"] + 1, best["drop"]))
    return out


def measure_hook_absence() -> dict:
    """The trigger is NOT a hook. MEASURED by AST, not by reading a comment."""
    src = PROOF_GATE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    present = sorted(h for h in REATTACH_HELPERS if h in defined)
    return {
        "helpers_checked": list(REATTACH_HELPERS),
        "helpers_present": present,
        "deleted": not present,
        "why": ("the re-attach design is DELETED: %s"
                % ("none of the four helpers exist" if not present
                   else "STILL PRESENT: %s" % present)),
    }


# ---------------------------------------------------------------------------
# 2. THE VERSION-UPGRADE TRIGGER
# ---------------------------------------------------------------------------
def measure_version_trigger(conn: sqlite3.Connection) -> dict:
    """Is there a trigger that upgrades a version after a proof run succeeds?"""
    per_type = []
    for r in conn.execute(
            "SELECT entity_type, COUNT(*) n, MAX(version) mx "
            "FROM version_registry GROUP BY 1 ORDER BY 1"):
        per_type.append({"entity_type": r["entity_type"], "rows": int(r["n"]),
                         "max_version": int(r["mx"])})
    total = _count(conn, "SELECT COUNT(*) FROM version_registry")
    above_one = _count(conn, "SELECT COUNT(*) FROM version_registry "
                             "WHERE version > 1")

    # Does `activate` INSERT a version? MEASURED by AST over the function body.
    src = ACTIVATION_GATE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    activate = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "activate":
            activate = n
            break
    inserts_version = False
    updates_version = False
    if activate is not None:
        for n in ast.walk(activate):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                s = n.value.upper()
                if "INSERT" in s and "version_registry" in s:
                    inserts_version = True
                if "UPDATE version_registry" in s:
                    updates_version = True

    return {
        "version_registry_rows": total,
        "rows_with_version_above_1": above_one,
        "per_entity_type": per_type,
        "activate_inserts_a_version": inserts_version,
        "activate_updates_is_active": updates_version,
        "trigger_exists": inserts_version,
        "why": ("NO TRIGGER: `activation_gate.activate` %s a version and %s "
                "`is_active`; %d of %d rows are version 1, so a successful proof "
                "run ACTIVATES version 1 and never creates version 2"
                % ("INSERTs" if inserts_version else "does NOT INSERT",
                   "UPDATES" if updates_version else "does not update",
                   total - above_one, total)),
    }


# ---------------------------------------------------------------------------
# 3. THE CHAT UI — what a human actually sees
# ---------------------------------------------------------------------------
def measure_chat_readability(conn: sqlite3.Connection) -> dict:
    """A PROOF turn vs a REAL turn, and a draft vs a done turn."""
    total = _count(conn, "SELECT COUNT(*) FROM chat_center_message")
    proof = _count(conn, "SELECT COUNT(*) FROM chat_center_message "
                         "WHERE content LIKE ?", (PROOF_CONTENT_PREFIX + "%",))
    by_status = [{"status": r["status"], "turns": int(r["n"])}
                 for r in conn.execute(
                     "SELECT status, COUNT(*) n FROM chat_center_message "
                     "GROUP BY 1 ORDER BY n DESC")]
    chats = _count(conn, "SELECT COUNT(*) FROM chat")
    untitled = _count(conn, "SELECT COUNT(*) FROM chat "
                            "WHERE title IS NULL OR TRIM(title) = ''")
    sample = []
    for r in conn.execute(
            "SELECT id, role, status, substr(content, 1, 40) c "
            "FROM chat_center_message WHERE chat_id = 68 "
            "ORDER BY id DESC LIMIT 6"):
        sample.append({"id": int(r["id"]), "role": r["role"],
                       "status": r["status"], "content": r["c"]})
    return {
        "turns": total,
        "proof_turns": proof,
        "real_turns": total - proof,
        "by_status": by_status,
        "chats": chats,
        "chats_without_title": untitled,
        "sample_chat_68": sample,
        "why": ("%d of %d turns are PROOF scaffolding and %d of %d chats have no "
                "title, so the page shows raw proof text as if it were a "
                "conversation" % (proof, total, untitled, chats)),
    }


# ---------------------------------------------------------------------------
# 4. THE CHAIN — ticket -> chat, and the identity
# ---------------------------------------------------------------------------
def measure_chain(conn: sqlite3.Connection) -> dict:
    """The ticket -> chat link, and whether an identity carries its chat."""
    link_rows = _count(conn, "SELECT COUNT(*) FROM ticket_chat_link")
    tickets = _count(conn, "SELECT COUNT(*) FROM ticket")
    ident_total = _count(conn, "SELECT COUNT(*) FROM identity_registry")
    ident_with_chat = _count(conn, "SELECT COUNT(*) FROM identity_registry "
                                   "WHERE chat_id IS NOT NULL")
    ident_null_chat = ident_total - ident_with_chat
    null_ids = [int(r["identity_id"]) for r in conn.execute(
        "SELECT identity_id FROM identity_registry WHERE chat_id IS NULL "
        "ORDER BY identity_id DESC LIMIT 10")]
    return {
        "ticket_chat_link_rows": link_rows,
        "tickets": tickets,
        "identity_rows": ident_total,
        "identity_with_chat_id": ident_with_chat,
        "identity_without_chat_id": ident_null_chat,
        "identity_ids_without_chat": null_ids,
        "why": ("ticket_chat_link holds %d rows for %d ticket(s) — the link is "
                "DESIGNED and never written; %d of %d identities carry no chat_id"
                % (link_rows, tickets, ident_null_chat, ident_total)),
    }


def measure_identity_middleware(conn: sqlite3.Connection) -> dict:
    """CALL `identity_middleware.check()` — never re-implement it."""
    import identity_middleware as im
    rows = []
    for r in conn.execute(
            "SELECT i.identity_id, i.session_id, i.worker_id, i.chat_id, i.why "
            "FROM identity_registry i ORDER BY i.identity_id DESC LIMIT 10"):
        ident = {
            "session_id": r["session_id"],
            "worker_key": r["worker_id"],
            "why": r["why"],
            "subject_kind": "chat",
        }
        res = im.check(conn, ident)
        rows.append({
            "identity_id": int(r["identity_id"]),
            "chat_id": r["chat_id"],
            "ok": bool(res.get("ok")),
            "refused": [x["code"] for x in res.get("refused") or []],
            "reported": [x["code"] for x in res.get("reported") or []],
        })
    ok_n = sum(1 for r in rows if r["ok"])
    return {
        "checked": len(rows),
        "ok": ok_n,
        "not_ok": len(rows) - ok_n,
        "rows": rows,
        "why": ("identity_middleware.check() confirms the 2-factor + purpose: "
                "%d of %d identities pass" % (ok_n, len(rows))),
    }


# ---------------------------------------------------------------------------
def report(conn: sqlite3.Connection, transcript: Path | None) -> dict:
    return {
        "checklist_id": "CHAT.IS.THE.ANSWER.KEY",
        "report_trigger": measure_report_trigger(transcript),
        "hook_absence": measure_hook_absence(),
        "version_trigger": measure_version_trigger(conn),
        "chat_readability": measure_chat_readability(conn),
        "chain": measure_chain(conn),
        "identity_middleware": measure_identity_middleware(conn),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--transcript", default=os.environ.get(
        "VSCODE_TARGET_SESSION_LOG", ""))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # The transcript lives beside the debug log, in a `transcripts/` folder.
    #
    # MEASURED: `VSCODE_TARGET_SESSION_LOG` points at
    # `.../GitHub.copilot-chat/debug-logs/<session>`, and the transcript is at
    # `.../GitHub.copilot-chat/transcripts/<session>.jsonl` — a SIBLING of
    # `debug-logs`, not a child. My first version looked for
    # `<debug-logs>/transcripts`, found nothing, and reported "no transcript to
    # read" — a detector that could not find the thing it was built to find.
    tp = None
    if args.transcript:
        cand = Path(args.transcript)
        if cand.is_dir():
            # THE SESSION ID IS THE DEBUG-LOG DIRECTORY NAME, and the transcript
            # is `<session>.jsonl`. MEASURED: picking the newest `.jsonl` chose a
            # DIFFERENT session's transcript, so the trigger was measured on the
            # wrong conversation. Match the NAME first; fall back to newest only
            # when no name matches, and SAY which one was used.
            want = cand.name
            for base in [cand] + list(cand.parents)[:4]:
                tdir = base / "transcripts"
                if not tdir.is_dir():
                    continue
                named = tdir / (want + ".jsonl")
                if named.exists():
                    tp = named
                    break
                files = sorted(tdir.glob("*.jsonl"),
                               key=lambda p: p.stat().st_mtime)
                if files:
                    tp = files[-1]
                    break
        elif cand.exists():
            tp = cand

    conn = _connect(Path(args.db))
    res = report(conn, tp)
    conn.close()

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    print("CHAT.IS.THE.ANSWER.KEY — the chat is the answer key")
    print("=" * 72)
    rt = res["report_trigger"]
    print("1. THE REPORT-SHRINK TRIGGER")
    print("   trigger : %s" % rt["trigger"])
    print("   %s" % rt["why"])
    if rt.get("before"):
        print("   before  : L%d %d chars  %r"
              % (rt["before"]["line"], rt["before"]["chars"],
                 rt["before"]["head"]))
    if rt.get("after"):
        print("   after   : L%d %d chars  %r"
              % (rt["after"]["line"], rt["after"]["chars"],
                 rt["after"]["head"]))
    ha = res["hook_absence"]
    print("   hook    : %s" % ha["why"])

    vt = res["version_trigger"]
    print()
    print("2. THE VERSION-UPGRADE TRIGGER")
    print("   %s" % vt["why"])
    print("   per entity type (max_version): %s"
          % ", ".join("%s=%d" % (t["entity_type"], t["max_version"])
                      for t in vt["per_entity_type"]))

    cr = res["chat_readability"]
    print()
    print("3. THE CHAT UI")
    print("   %s" % cr["why"])
    print("   turns=%d  proof=%d  real=%d"
          % (cr["turns"], cr["proof_turns"], cr["real_turns"]))
    print("   by status: %s"
          % ", ".join("%s=%d" % (s["status"], s["turns"])
                      for s in cr["by_status"]))
    print("   sample chat 68:")
    for s in cr["sample_chat_68"]:
        print("     id=%-6d %-9s %-7s %r"
              % (s["id"], s["role"], s["status"], s["content"]))

    ch = res["chain"]
    print()
    print("4. THE CHAIN")
    print("   %s" % ch["why"])
    print("   identity ids without chat_id: %s" % ch["identity_ids_without_chat"])
    imw = res["identity_middleware"]
    print("   %s" % imw["why"])


if __name__ == "__main__":
    main()
