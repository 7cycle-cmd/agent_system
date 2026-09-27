# -*- coding: utf-8 -*-
"""_register_conversation_stepbar.py — register the 2 missing step-bar words and
their 3 element rows.

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "highlight bar -> button for shortcut to index and list"
    "do it now"

WHAT THIS FIXES, MEASURED
-------------------------
The step bar at `llm_task_monitor_ui/src/conversation-center.js:239` renders FIVE
controls. MEASURED, `ui_element_register` holds only THREE of them for
`page_key='conversation.value'`:

    registered     conversation.step.new, conversation.step.list,
                   conversation.step.index.title
    NOT registered  the `Data` control, the `Refresh` control

and the `ui-standard` skill this repo now enforces says **"Rule 1: Every visible
element is a REGISTERED row in `ui_element_register`"**, so the bar is already
failing the standard that governs it: **3 of 5**.

TWO VISIBLE LABELS NAME UNREGISTERED WORDS. MEASURED against the 1514 rows of
`terminology_register`: `index`, `list`, `chat` and `step` EXIST; **`data` and
`refresh` are MISSING**. And `ui_element_register.add_element` REFUSES an unknown
`term_key` (`ui_element_register.py:224-230`), so the two missing rows **cannot
be added until their words are registered**. THE ORDER IS FORCED, NOT CHOSEN.

WHY `data` CARRIES A NAMED COLLISION
------------------------------------
`data` is the single most overloaded word in computing. Its definition here must
say WHICH sense it is, or a later reader picks the wrong one — the same treatment
`index` got in `plan_CONVERSATION.INDEX.IS.START.md` F5, for the same reason.

RUN:
    .\\.venv\\Scripts\\python.exe _register_conversation_stepbar.py --dry-run
    .\\.venv\\Scripts\\python.exe _register_conversation_stepbar.py --apply
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# ---- THE TWO WORDS --------------------------------------------------------
# A definition that merely restates the name is the defect `terminology_register`
# exists to remove, so each says WHAT it is, WHAT it is NOT, and where it lives.
TERMS: tuple[dict[str, str], ...] = (
    {
        "term_key": "data",
        "definition": (
            "The ENGINEER'S VIEW of the conversation page: the four levels, the "
            "prefix-to-plan chain and the known gaps, laid out as numbers. It is "
            "the THIRD step of the conversation wizard, reached by the step bar "
            "control labelled `Data`. It is NOT the chats (which is `list`) and "
            "NOT an exchange thread (which is `conversation`). NAMED COLLISION: "
            "`data` normally means any stored or measured facts at all, so this "
            "word is far broader than what it names here -- on this page it names "
            "ONE diagnostic step, and other pages already use the same word for "
            "their own tabs (task-center, worker, ticket-center). The collision is "
            "recorded so a later reader is told rather than left to guess."
        ),
        "cite_ref": "llm_task_monitor_ui/src/conversation-center.js:1363",
    },
    {
        "term_key": "refresh",
        "definition": (
            "The ACTION that RE-READS every source this page renders, and redraws "
            "the step the human is looking at. It is the rightmost control in the "
            "conversation step bar, marked `ml-auto` so it sits apart from the "
            "steps. It is NOT a step: pressing it does not change which step is "
            "shown, and its address does not change. It is NOT `loadAll` itself -- "
            "`loadAll` is the function, `Refresh` is the label a human reads."
        ),
        "cite_ref": "llm_task_monitor_ui/src/conversation-center.js:270",
    },
)

TERM_CITE = "_register_conversation_stepbar.py:TERMS"

# ---- THE THREE MISSING ELEMENT ROWS ---------------------------------------
# `pct` is the unit MEASURED for a step-bar control by the previous plan, and the
# reason stands: rule 1 of `ui-standard` (`label_registered`) has
# `metric_unit = pct`. `unit_register.ui_label` EXISTS but is `is_active = 0`, and
# `_known_units` reads `is_active = 1` — flipping it by hand would be the
# activation gate proving itself.
PAGE_KEY = "conversation.value"
UI_CITE = "_register_conversation_stepbar.py:ELEMENTS"
UI_UNIT = "pct"
# WHERE EACH ELEMENT IS RENDERED. MEASURED: the step bar's controls are rendered
# by `conversation-center.js`, and the caption strip's two shortcuts by `app.js`.
# ONE constant for both was WRONG: `source_ref` is what makes a row checkable
# against the page, and a row pointing at the wrong file cannot be checked.
SOURCE_REF_STEP = "llm_task_monitor_ui/src/conversation-center.js:stepbar"
SOURCE_REF_STRIP = "llm_task_monitor_ui/src/app.js:6471"

# (element_key, element_kind, rendered_text, term_key, population, why)
ELEMENTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    # THE TWO STRIP SHORTCUTS (2026-09-27). THE HUMAN, verbatim:
    #     "Conversation Center · one index · chat → chat_main →
    #      chat_center_message → identity_register / is the position for tha
    #      button"
    #
    # MEASURED BEFORE: the strip was ONE `<span>` with 0 `<button>`, and it NAMED
    # `index` in its own text while offering no way to reach it. A label that
    # names a destination and is not a control.
    ("conversation.strip.new", "action", "+ New chat", "index",
     "one shortcut in the conversation caption strip",
     "It opens the START step at /llm-tasks/conversation/index, where a new "
     "chat's first question is typed."),
    ("conversation.strip.list", "action", "Chats", "list",
     "one shortcut in the conversation caption strip",
     "It opens the chat LIST at /llm-tasks/conversation/list, where a row is "
     "picked."),
    ("conversation.step.chat", "action", "2 \u00b7 One chat", "chat",
     "one control in the conversation step bar",
     "It returns to the chat that is already open. It is a BUTTON and it is "
     "DISABLED until a row has been picked in step 1, because before that there "
     "is no chat to return to."),
    ("conversation.step.data", "action", "Data", "data",
     "one control in the conversation step bar",
     "It opens the engineer's view of this page, where every number comes from "
     "/api/conversation_center/index."),
    ("conversation.step.refresh", "action", "Refresh", "refresh",
     "one control in the conversation step bar",
     "It re-reads every source this page renders. It does not change which step "
     "is shown, so it never moves the human."),
)


def log(msg: str) -> None:
    print(msg, flush=True)


def seed_ui_elements(conn: sqlite3.Connection) -> dict:
    import ui_element_register as uer

    out: dict = {"ok": False, "rows": [], "errors": []}
    for ekey, kind, text, term_key, population, why in ELEMENTS:
        # THE SOURCE IS PER ELEMENT, not one constant for all of them.
        src_ref = (SOURCE_REF_STRIP if ekey.startswith("conversation.strip.")
                   else SOURCE_REF_STEP)
        # `add_element` is IDEMPOTENT on `element_key`, so a row seeded with a
        # DIFFERENT term_key comes back `created: False` and is NEVER corrected.
        # An idempotent writer that cannot correct a row cannot repair its own
        # mistake, so the correction is explicit here -- and it covers `source_ref`
        # too, because a row pointing at the WRONG file cannot be checked.
        prev = conn.execute(
            "SELECT element_id, rendered_text, term_key, source_ref "
            "FROM ui_element_register WHERE element_key=?", (ekey,)).fetchone()
        if prev and (str(prev["rendered_text"]) != text
                     or str(prev["term_key"]) != term_key
                     or str(prev["source_ref"]) != src_ref):
            conn.execute(
                "UPDATE ui_element_register SET rendered_text=?, user_label=?, "
                "term_key=?, unit_key=?, source_ref=?, updated_at=datetime('now') "
                "WHERE element_id=?",
                (text, text, term_key, UI_UNIT, src_ref,
                 int(prev["element_id"])))
            out["rows"].append({"element_key": ekey, "created": False,
                                "updated": True,
                                "old_term_key": str(prev["term_key"])})
            continue
        r = uer.add_element(
            conn,
            element_key=ekey,
            page_key=PAGE_KEY,
            element_kind=kind,
            rendered_text=text,
            user_label=text,
            term_key=term_key,
            unit_key=UI_UNIT,
            population=population,
            why_clickable=why,
            source_ref=src_ref,
            cite_ref=UI_CITE,
            commit=False)
        if not r.get("ok"):
            out["errors"].append("%s: %s %s"
                                 % (ekey, r.get("code"), r.get("message")))
            continue
        out["rows"].append({"element_key": r["element_key"],
                            "created": r.get("created"), "updated": False})

    # NO COMMIT / ROLLBACK HERE. Rolling back inside this function DISCARDS the
    # term insert the UI seeding depends on, so a dry-run would report
    # UNKNOWN_TERM_KEY for a term the SAME RUN just inserted — a failure about the
    # ORDER, not about the data.
    out["ok"] = not out["errors"]
    return out


def run(db_path: str | Path | None = None, *, apply: bool = False) -> dict:
    import terminology_registry as tr

    # A DRY-RUN MUST NOT TOUCH THE LIVE DB, AND `commit=False` IS NOT ENOUGH.
    #
    # MEASURED (recorded in `_register_conversation_index_term.py`): `add_term`
    # COMMITS anyway, because it calls `check_instance_name`, which calls
    # `declared_instance_values`, which calls `ensure_instance_schema`, which calls
    # `conn.commit()`. A gate that commits defeats the caller's `commit=False`.
    # CONSEQUENCE, MEASURED: two `--dry-run` runs wrote `list` and `start` into
    # the LIVE db.
    #
    # THE FIX IS HERE, NOT IN THE REGISTER: `terminology_registry.py` is outside
    # this plan's allowlist, so this script makes its OWN dry-run safe by
    # operating on a COPY. The register defect is recorded for a later plan.
    src = Path(db_path or DEFAULT_DB)
    tmp_path: Path | None = None
    if not apply:
        fd, name = tempfile.mkstemp(prefix="dryrun_", suffix=".db")
        os.close(fd)
        tmp_path = Path(name)
        shutil.copy2(src, tmp_path)
        src = tmp_path

    conn = sqlite3.connect(str(src), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        out: dict = {"ok": False, "apply": apply, "rows": [], "errors": []}
        for spec in TERMS:
            key = spec["term_key"]
            existing = conn.execute(
                "SELECT term_id FROM terminology_register WHERE term_key=?",
                (key,)).fetchone()
            if existing:
                out["rows"].append({"term_key": key,
                                    "term_id": int(existing["term_id"]),
                                    "action": "already registered"})
                continue
            r = tr.add_term(conn, key, definition=spec["definition"],
                            cite_ref=spec["cite_ref"], term_kind="entity",
                            taxonomy_level="NA", commit=False)
            if not r.get("ok"):
                out["errors"].append("%s: %s %s"
                                     % (key, r.get("code"), r.get("message")))
            else:
                out["rows"].append({"term_key": key,
                                    "term_id": r.get("term_id"),
                                    "action": "registered"})

        # VERIFY and SEED before the ONE commit/rollback, so a dry-run sees the
        # terms it just inserted.
        missing = [s["term_key"] for s in TERMS if not conn.execute(
            "SELECT 1 FROM terminology_register WHERE term_key=?",
            (s["term_key"],)).fetchone()]
        out["missing_after"] = missing
        ui = seed_ui_elements(conn)
        out["ui"] = ui

        if apply:
            conn.commit()
            out["committed"] = True
        else:
            conn.rollback()
            out["committed"] = False

        out["ok"] = (not out["errors"] and not missing and ui["ok"])
        return out
    finally:
        conn.close()
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="register the conversation step-bar words and elements")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    r = run(args.db, apply=bool(args.apply))
    log("apply     : %s" % r["apply"])
    for row in r["rows"]:
        log("  %-10s id=%-6s %s"
            % (row["term_key"], row["term_id"], row["action"]))
    log("missing   : %s" % r["missing_after"])
    log("ui rows   : %d" % len(r.get("ui", {}).get("rows") or []))
    for u in (r.get("ui", {}).get("rows") or []):
        log("   %-34s created=%s updated=%s"
            % (u["element_key"], u["created"], u.get("updated")))
    log("committed : %s" % r.get("committed"))
    if r["errors"] or (r.get("ui", {}).get("errors")):
        log("ERRORS:")
        for e in (r["errors"] + (r.get("ui", {}).get("errors") or [])):
            log("   %s" % e)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
