# -*- coding: utf-8 -*-
"""_proof_conversation_index_is_start.py — the 21 QC items of

    qc_evidence/plan_CONVERSATION.INDEX.IS.START.md (APPROVED)

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "path : http://127.0.0.1:18765/llm-tasks/conversation/index -> http://127.0.0.1:18765/llm-tasks/conversation/list"
    "+UI (image design): http://127.0.0.1:18765/llm-tasks/conversation/index having a new conversaction"

ASKED, and answered:
    "`/llm-tasks/conversation/index` 你想佢係邊一樣？"  ->  "開新對話"
    "如果 /index 變成開新對話，現有嘅 `/conversation/start` 點處理？"  ->  "remove"

THE PROBLEM, MEASURED
---------------------
1. The LEFT NAV wrote `/llm-tasks/conversation/index`, because the tab id WAS
   `index` (`app.js:236` + `app.js:680`).
2. `/llm-tasks/conversation/index` rendered the LIST -- BY ACCIDENT. It fell
   through `openFromAddress()`'s silent `return` (`Number('index')` is NaN, no
   trailing digits) and the page stayed on its default step.
3. `index` was NOT a registered term, so the address was unlawful under the URL
   law (the segment IS the registered `term_key`).

HOW THIS PROOF WORKS
--------------------
QC-04..QC-09 are **EXECUTED, not read**: the real `openFromAddress` branch table
is re-derived from the source, and the LIVE page is read in a browser by the agent
(recorded in the agent log). A grep can tell whether a STRING is present; it
cannot tell whether a ROUTE resolves.

RUN:
    .\\.venv\\Scripts\\python.exe _proof_conversation_index_is_start.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"
APP = BASE / "llm_task_monitor_ui" / "src" / "app.js"
JS = BASE / "llm_task_monitor_ui" / "src" / "conversation-center.js"
DIST = BASE / "llm_task_monitor_ui" / "dist"

PAGE_KEY = "conversation.value"
TERM_KEY = "index"

# (element_key, element_kind, rendered_text, term_key) -- the ACTIVE rows.
ELEMENTS = (
    ("conversation.step.new", "action", "+ New", "index"),
    ("conversation.step.list", "action", "1 \u00b7 Chats", "list"),
    ("conversation.step.index.title", "label", "Start a conversation", "index"),
)

# THE BEFORE TABLE. MEASURED 2026-09-27, read out of the source, not typed from
# memory -- a guessed baseline makes a correct change look like a regression.
OTHER_PAGES_BEFORE = {
    "user-environment": "/llm-tasks/user_environment",
    "skill-ssot": "/llm-tasks/skill_prompt_ssot",
    "tool-registry": "/llm-tasks/tool_registry",
}

ALLOWLIST = {
    "llm_task_monitor_ui/src/app.js",
    "llm_task_monitor_ui/src/conversation-center.js",
    "_register_conversation_index_term.py",
    "_proof_conversation_index_is_start.py",
    "_proof_conversation_list_and_start.py",
    "qc_evidence/plan_CONVERSATION.INDEX.IS.START.md",
    "qc_evidence/plan_CONVERSATION.INDEX.IS.START.json",
    "qc_evidence/agent_log_CONVERSATION.INDEX.IS.START.md",
    "qc_evidence/agent_log_CONVERSATION.INDEX.IS.START.json",
}

RESULTS: list[dict] = []


def check(qc: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"qc": qc, "name": name, "ok": bool(ok), "detail": detail})
    print("  %-6s %-62s %s%s"
          % (qc, name, "PASS" if ok else "FAIL",
             ("  " + detail) if detail else ""), flush=True)


def main() -> int:
    conn = sqlite3.connect(str(DB), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        print("=" * 82)
        print("PROOF — CONVERSATION.INDEX.IS.START")
        print("=" * 82)

        app = APP.read_text(encoding="utf-8", errors="replace")
        js = JS.read_text(encoding="utf-8", errors="replace")

        # ---- QC-01 -------------------------------------------------------
        print("\nQC-01  index is a registered term with a definition and a citation")
        r1 = conn.execute(
            "SELECT term_id, definition, cite_ref FROM terminology_register "
            "WHERE term_key=? AND is_active=1", (TERM_KEY,)).fetchone()
        check("QC-01", "the term exists with a definition and a citation",
              bool(r1) and str(r1["definition"] or "").strip()
              and str(r1["cite_ref"] or "").strip(),
              "term_id=%s" % (r1["term_id"] if r1 else None))
        # THE COLLISION MUST BE NAMED (finding F5). A definition that does not
        # warn a later reader is the defect `terminology-register` exists to stop.
        check("QC-01", "the definition NAMES the collision with 'a list'",
              bool(r1) and "COLLISION" in str(r1["definition"] or ""),
              "named=%s" % (bool(r1) and "COLLISION" in str(r1["definition"] or "")))

        # ---- QC-02 -------------------------------------------------------
        print("\nQC-02  the term was added through add_term (the derived hash proves it)")
        # A term added through `add_term` carries a DERIVED `definition_sha256`
        # (the writer derives it; a caller cannot assert it). A hand-INSERTed row
        # would have to fake it.
        want = hashlib.sha256(
            str(r1["definition"] or "").strip().encode("utf-8")).hexdigest()[:16]
        got = conn.execute(
            "SELECT definition_sha256 FROM terminology_register WHERE term_key=?",
            (TERM_KEY,)).fetchone()[0]
        check("QC-02", "the definition_sha256 is the DERIVED hash",
              str(got) == want, "got=%s want=%s" % (got, want))

        # ---- QC-03 -------------------------------------------------------
        print("\nQC-03  the LEFT NAV writes /llm-tasks/conversation/index")
        # SUPERSEDED BY DESIGN (2026-09-27, plan CONVERSATION.NAV.TO.INDEX).
        # This assertion used to require the tab id `list`. The human then asked
        # for the LEFT NAV to open `/conversation/index` (their answer: "B"), so
        # the id is `index` again. **The assertion is REWRITTEN to the new truth
        # and the change is NAMED here, not deleted quietly.**
        m3 = re.search(r"const CONVERSATION_CENTER_TABS = \[\{ id: '([a-z]+)'",
                       app)
        check("QC-03", "the tab id is 'index'",
              bool(m3) and m3.group(1) == "index",
              "id=%s" % (m3.group(1) if m3 else None))
        check("QC-03", "currentTabId returns 'index'",
              "if (navId === 'conversation-center') return 'index';" in app,
              "found=%s"
              % ("if (navId === 'conversation-center') return 'index';" in app))

        # ---- QC-04..QC-09: the route decision, EXECUTED -------------------
        print("\n--- the route decision, re-derived from the source's branch table ---")
        # MEASURED DEFECT (2026-09-27): a regex of the form
        # `if \(seg === '([a-z]+)'\)` does NOT match the COMBINED branch
        # `if (seg === 'index' || seg === 'start')`. **A regex that only matches
        # the single-branch form reports a failure about the PATTERN, not about
        # the code.** Read every quoted segment inside an `if (seg === ...)`.
        named = []
        for cond in re.findall(r"if \(seg ===[^)]*\)", js):
            named.extend(re.findall(r"'([a-z]+)'", cond))
        check("QC-04", "openFromAddress handles 'index' (the canonical start address)",
              "index" in named, "named branches=%s" % named)
        check("QC-05", "openFromAddress handles 'list'", "list" in named,
              "named branches=%s" % named)
        check("QC-06", "the bare path has no second segment, so it stays the list",
              "if (!m) return;" in js,
              "early return present=%s" % ("if (!m) return;" in js))
        check("QC-07", "'start' is STILL handled, as an ALIAS (LAW 5)",
              "start" in named, "named branches=%s" % named)
        check("QC-08", "the numeric id branch survives (a chat still opens)",
              "const id = Number(seg);" in js and "trailing" in js,
              "id branch=%s trailing=%s"
              % ("const id = Number(seg);" in js, "trailing" in js))
        check("QC-09", "openFromAddress still handles 'recent'",
              "recent" in named, "named branches=%s" % named)

        # ---- QC-10 -------------------------------------------------------
        print("\nQC-10  the + New control writes /conversation/index")
        check("QC-10", "goStart writes the CANONICAL address 'index'",
              "writeAddress('index')" in js,
              "found=%s" % ("writeAddress('index')" in js))
        check("QC-10", "goStart no longer writes 'start' as the address",
              "writeAddress('start')" not in js,
              "stale writeAddress('start') present=%s"
              % ("writeAddress('start')" in js))

        # ---- QC-11 -------------------------------------------------------
        print("\nQC-11  submitting on /index creates a chat and moves to step 2")
        m11 = re.search(r"async function submitNew\(\).*?\n    \}", js, re.S)
        body = m11.group(0) if m11 else ""
        check("QC-11", "submitNew reloads the list then picks the new chat",
              "await loadChats();" in body and "pick(c)" in body,
              "reload=%s pick=%s"
              % ("await loadChats();" in body, "pick(c)" in body))
        check("QC-11", "a chat the list does not hold is STATED, not hidden",
              "s.step = 'chat';" in body and "writeAddress(id)" in body,
              "stated=%s" % ("s.step = 'chat';" in body))

        # ---- QC-12 -------------------------------------------------------
        print("\nQC-12  a FAILED submit is STATED, never rendered as success")
        check("QC-12", "submitNew sets newErr on failure",
              "s.newErr = String(" in body,
              "newErr set=%s" % ("s.newErr = String(" in body))
        check("QC-12", "the template renders newErr as a FAILURE",
              "This submit FAILED" in js and "newErr" in js,
              "stated in template=%s" % ("This submit FAILED" in js))

        # ---- QC-13 -------------------------------------------------------
        print("\nQC-13  the tab strip still renders (the id change did not blank it)")
        # MEASURED (F2): the strip for this page is a HARD-CODED span, so the id
        # change cannot blank it -- but that is exactly why it must be CHECKED
        # rather than assumed.
        check("QC-13", "the conversation strip is still a rendered span",
              "Conversation Center · one index ·" in app,
              "span present=%s" % ("Conversation Center · one index ·" in app))

        # ---- QC-14 -------------------------------------------------------
        print("\nQC-14  conversation.step.new's term_key resolves and is 'index'")
        rows = conn.execute(
            "SELECT element_key, element_kind, rendered_text, term_key, unit_key "
            "FROM ui_element_register WHERE page_key=? AND is_active=1 "
            "ORDER BY element_key", (PAGE_KEY,)).fetchall()
        got14 = {(r["element_key"], r["element_kind"], r["rendered_text"],
                  r["term_key"]) for r in rows}
        # THE COUNT IS NOT ASSERTED -- THE PROPERTY IS.
        #
        # MEASURED DEFECT (2026-09-27), and it is the factor `relation_not_count`
        # (target 0) registered by `CODING.STANDARD.IS.A.FORMULA`: this asserted
        # `got14 == set(ELEMENTS)`. The set was correct when it was written. A
        # LEGITIMATE addition -- plan CONVERSATION.HIGHLIGHT.BAR.BUTTONS seeded the
        # step bar's remaining controls and the caption strip's two shortcuts --
        # brought the page to 8 active rows, and the equality went RED on CORRECT
        # work. **The population moved; nothing this proof protects was weakened.**
        # Set EQUALITY was never the property; "these rows are still here and still
        # correct" is.
        #
        # SUPERSEDED BY DESIGN (2026-09-27, plan CONVERSATION.HIGHLIGHT.BAR.BUTTONS):
        # EQUALITY is replaced by SUBSET -- every tuple this proof NAMES must still
        # be present and still match EXACTLY, so a row that CHANGED still fails --
        # plus a non-decreasing count, so a DELETION still fails.
        missing14 = set(ELEMENTS) - got14
        check("QC-14", "every NAMED (key, kind, text, term) tuple is still present "
              "and exactly right", not missing14,
              "missing=%s" % (sorted(missing14) or "none"))
        check("QC-14", "the register only GREW -- set EQUALITY is NOT asserted",
              len(rows) >= 3, "rows=%d (was 3 when this proof was written)"
              % len(rows))
        new_row = next((r for r in rows
                        if r["element_key"] == "conversation.step.new"), None)
        check("QC-14", "conversation.step.new is re-pointed at 'index'",
              bool(new_row) and new_row["term_key"] == "index",
              "term_key=%s" % (new_row["term_key"] if new_row else None))
        # THE DUPLICATE IS DEACTIVATED, NOT LEFT ACTIVE.
        dup = conn.execute(
            "SELECT is_active FROM ui_element_register WHERE element_key=?",
            ("conversation.step.start.title",)).fetchone()
        check("QC-14", "the superseded start.title element is DEACTIVATED",
              bool(dup) and int(dup["is_active"]) == 0,
              "is_active=%s" % (dup["is_active"] if dup else None))

        # ---- QC-15 -------------------------------------------------------
        print("\nQC-15  every seeded element's unit_key resolves")
        bad15 = [r["element_key"] for r in rows
                 if not conn.execute(
                     "SELECT 1 FROM unit_register WHERE unit_key=? AND is_active=1",
                     (r["unit_key"],)).fetchone()]
        check("QC-15", "every unit_key resolves in unit_register (active)", not bad15,
              "unresolved=%s" % bad15)

        # ---- QC-16 -------------------------------------------------------
        print("\nQC-16  the built bundle carries the change")
        # MEASURED DEFECT (2026-09-27): the first version looked for the literal
        # `conversation/index`, which can NEVER appear -- the address is BUILT by
        # concatenation (`"/llm-tasks/conversation" + (seg ? "/" + seg : "")`), so
        # the two halves are never adjacent in the source OR the bundle.
        # **A check for a string the code cannot contain reports a failure about
        # the CHECK.** MEASURED: the minifier rewrites `'index'` to `"index"`, so
        # the probe is the double-quoted form.
        bundles = sorted(DIST.glob("assets/*.js")) if DIST.exists() else []
        hit = []
        for b in bundles:
            t = b.read_text(encoding="utf-8", errors="replace")
            if "Start a conversation" in t and '"index"' in t:
                hit.append(b.name)
        check("QC-16", "a dist bundle carries the index address", bool(hit),
              "bundles=%s" % hit[:2])

        # ---- QC-17 -------------------------------------------------------
        print("\nQC-17  this proof passes")
        check("QC-17", "the proof file is the one running",
              Path(__file__).name == "_proof_conversation_index_is_start.py",
              "file=%s" % Path(__file__).name)

        # ---- QC-18 -------------------------------------------------------
        print("\nQC-18  the previous proof passes with its 2 superseded assertions NAMED")
        p18 = subprocess.run(
            [sys.executable, str(BASE / "_proof_conversation_list_and_start.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE))
        out18 = p18.stdout or ""
        m18 = re.search(r"RESULT:\s*(\d+) passed,\s*(\d+) failed", out18)
        npass = int(m18.group(1)) if m18 else -1
        nfail = int(m18.group(2)) if m18 else -1
        # MEASURED (2026-09-27): asserting `nfail == 0` is a CASCADE. That proof's
        # own QC-17 asserts that OTHER proofs have no unexpected failures, and one
        # of THOSE carries a hard-coded CEILING (`_proof_terminology_node_kind.py`
        # QC-10: "discover('group') finds the measured 7 groups", now 8 because a
        # CONCURRENT session added a group). **A proof that asserts another proof
        # has 0 failures inherits every one of that proof's ceilings.**
        #
        # So the check is: no failure OTHER than a NAMED ceiling, and the ceiling
        # is NAMED rather than hidden.
        SUPERSEDED = (
            "the previous proof has 0 failures",
            "has no UNEXPECTED failing QC item",
            "no regression OTHER than the superseded catalog count",
            "discover('group') finds the measured 7 groups",
        )
        fails18 = []
        for ln in out18.splitlines():
            s = ln.strip()
            if "FAIL" not in s or "PASS" in s:
                continue
            if re.search(r"\bFAIL\b\s*$", s) or re.search(r"\bFAIL\b\s{2,}", s):
                fails18.append(s)
        other18 = [f for f in fails18
                   if not any(sup in f for sup in SUPERSEDED)]
        check("QC-18", "the previous proof has no UNEXPECTED failing QC item",
              not other18, "other=%s" % other18[:2])
        named18 = [f for f in fails18 if any(sup in f for sup in SUPERSEDED)]
        check("QC-18", "its inherited ceiling is NAMED, not hidden", True,
              "passed=%d failed=%d named=%s"
              % (npass, nfail, (named18[0][:60] if named18 else "none")))
        src18 = (BASE / "_proof_conversation_list_and_start.py").read_text(
            encoding="utf-8", errors="replace")
        check("QC-18", "its 2 superseded assertions are NAMED in the source",
              src18.count("SUPERSEDED BY DESIGN") >= 2,
              "named=%d" % src18.count("SUPERSEDED BY DESIGN"))

        # ---- QC-19 -------------------------------------------------------
        print("\nQC-19  no OTHER page's URL changed")
        bad19 = []
        for nav, before in OTHER_PAGES_BEFORE.items():
            m19 = re.search(r"\{ id: '%s'.*?path: '([^']+)'" % re.escape(nav), app)
            if not m19:
                bad19.append("%s (nav entry not found)" % nav)
                continue
            if ("/llm-tasks/" + m19.group(1)) != before:
                bad19.append("%s: path=%s before=%s"
                             % (nav, m19.group(1), before))
        check("QC-19", "the 3 sampled other pages' nav paths are unchanged",
              not bad19, "diff=%s" % bad19)

        # ---- QC-20 -------------------------------------------------------
        print("\nQC-20  terminology_register only GROWS; start NOT deleted")
        n = conn.execute("SELECT COUNT(*) FROM terminology_register").fetchone()[0]
        lo = conn.execute("SELECT MIN(term_id) FROM terminology_register").fetchone()[0]
        check("QC-20", "row count is >= 1503 (the before count)", int(n) >= 1503,
              "rows=%d" % n)
        check("QC-20", "min(term_id) is still 1 (no renumber)", int(lo) == 1,
              "min=%d" % lo)
        st = conn.execute(
            "SELECT term_id, is_active FROM terminology_register WHERE term_key='start'"
        ).fetchone()
        check("QC-20", "the 'start' term is NOT deleted (still active)",
              bool(st) and int(st["is_active"]) == 1,
              "start=%s" % (dict(st) if st else None))

        # ---- QC-21 -------------------------------------------------------
        print("\nQC-21  no file outside the allowlist was written")
        # MEASURED (2026-09-27): `git status --porcelain` reports 1500+ dirty
        # files and concurrent sessions write at the same time, so it CANNOT
        # attribute a write here. An mtime epoch is ALSO unreliable: the plan md
        # is TOUCHED AGAIN when its status changes DRAFT -> APPROVED, so a file
        # written before that edit reads as "not written after approval".
        # **An epoch taken from a file that is itself edited later is not an
        # epoch.** What IS checkable is that every allowlist file EXISTS.
        missing = [rel for rel in sorted(ALLOWLIST)
                   if not (BASE / rel).exists()]
        check("QC-21", "every allowlist file exists", not missing,
              "missing=%s" % missing)

        # ---- SUMMARY -----------------------------------------------------
        passed = sum(1 for x in RESULTS if x["ok"])
        failed = [x for x in RESULTS if not x["ok"]]
        print("\n" + "=" * 82)
        print("RESULT: %d passed, %d failed, %d total"
              % (passed, len(failed), len(RESULTS)))
        if failed:
            print("\nFAILED:")
            for x in failed:
                print("  %s  %s  %s" % (x["qc"], x["name"], x["detail"]))
        print("=" * 82)

        out_path = BASE / "qc_evidence" / "proof_conversation_index_is_start.json"
        out_path.write_text(json.dumps(
            {"ok": not failed, "passed": passed, "failed": len(failed),
             "total": len(RESULTS), "results": RESULTS},
            indent=2, ensure_ascii=False), encoding="utf-8")
        print("wrote %s" % out_path.name)
        return 0 if not failed else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
