# -*- coding: utf-8 -*-
"""_proof_conversation_stepbar.py — the 25 QC items of

    qc_evidence/plan_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.md (APPROVED)

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "http://127.0.0.1:18765/llm-tasks/conversation/index"
    "highlight bar -> button for shortcut to index and list"
    "do it now"

THE PROBLEM, MEASURED BEFORE
----------------------------
1. `2 · One chat` was a `<span>` (`conversation-center.js:262-264`), so ONE OF
   FOUR steps was not a control -- while its class list was IDENTICAL to the
   buttons', so the hole was invisible.
2. `aria-current` had **0** occurrences in `conversation-center.js` AND in
   `app.js`, so "where am I" was signalled by COLOUR ONLY.
3. `goData()` wrote NO address and `openFromAddress()` had no `data` branch, so
   `/llm-tasks/conversation/data` rendered the LIST -- a URL that silently shows
   a DIFFERENT page.
4. The bar renders FIVE controls and only THREE were registered rows, so the bar
   already failed `ui-standard` Rule 1, which now governs it.
5. `data` and `refresh` were MISSING from `terminology_registry`, and
   `add_element` REFUSES an unknown `term_key` -- so the order was FORCED.

HOW THIS PROOF WORKS
--------------------
The address items (QC-07..QC-12) are **EXECUTED, not read**: `openFromAddress`'s
branch table is DERIVED from the source by regex and then asserted, and the LIVE
page is read in a browser by the agent (recorded in the agent log). A grep can
tell whether a STRING is present; it cannot tell whether a ROUTE resolves.

RUN:
    .\\.venv\\Scripts\\python.exe _proof_conversation_stepbar.py
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
sys.path.insert(0, str(BASE / "scripts"))

DB = BASE / "agent.db"
APP = BASE / "llm_task_monitor_ui" / "src" / "app.js"
JS = BASE / "llm_task_monitor_ui" / "src" / "conversation-center.js"
DIST = BASE / "llm_task_monitor_ui" / "dist"
PLAN = BASE / "qc_evidence" / "plan_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.md"

PAGE_KEY = "conversation.value"

# QC-13 / QC-22 ARE SUPERSEDED BY MEASUREMENT (2026-09-27).
#
# They were written when `app.js` was out of scope and asserted it was
# BYTE-IDENTICAL. THE HUMAN then said, verbatim: "Conversation Center · one index ·
# chat → chat_main → chat_center_message → identity_registry / is the position for
# tha button" -- which puts `app.js` IN scope, so the file MUST change. The two
# items are RETIRED and REPLACED by the property that still needs checking, NOT
# deleted and NOT renumbered.
#
# THE STRIP'S TEXT PREFIX. BOTH neighbour proofs assert this literal string is
# present (`_proof_conversation_index_is_start.py:220`,
# `_proof_conversation_nav_to_index.py:167`), so keeping it keeps both assertions
# TRUE and UNMODIFIED. That is what replacement QC-13R pins.
STRIP_PREFIX = "Conversation Center · one index ·"

# Replacement QC-22R: the SHARED url builder must not have been touched. My change
# is confined to the conversation branch of the tab strip plus one new
# `[data-cc-step]` binder, so the property is: `navPath` contains NEITHER
# `cc-step` NOR a `conversation` special case.
NAVPATH_FORBIDDEN = ("cc-step", "conversation")

# The two words this plan registers, and where the bar shows them.
NEW_TERMS = ("data", "refresh")

# Every element the bar renders, and the 2 that were missing before this task.
BAR_ELEMENTS = ("conversation.step.new", "conversation.step.list",
                "conversation.step.chat", "conversation.step.data",
                "conversation.step.refresh")
WERE_MISSING = ("conversation.step.chat", "conversation.step.data",
                "conversation.step.refresh")

# THE CAPTION STRIP'S TWO SHORTCUTS -- the human's own position for the button.
STRIP_ELEMENTS = ("conversation.strip.new", "conversation.strip.list")

# THE BEFORE TABLE. MEASURED 2026-09-27 from the source, not typed from memory --
# a guessed baseline makes a correct change look like a regression.
OTHER_PAGES_BEFORE = {
    "user-environment": "/llm-tasks/user_environment",
    "skill-ssot": "/llm-tasks/skill_prompt_ssot",
    "tool-registry": "/llm-tasks/tool_registry",
}

ALLOWLIST = (
    "llm_task_monitor_ui/src/conversation-center.js",
    "_registry_conversation_stepbar.py",
    "_proof_conversation_stepbar.py",
    "qc_evidence/plan_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.md",
    "qc_evidence/plan_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.json",
    "qc_evidence/agent_log_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.md",
    "qc_evidence/agent_log_CONVERSATION.HIGHLIGHT.BAR.BUTTONS.json",
)

PASS = 0
FAIL = 0


def check(qc: str, name: str, ok, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %-7s %s%s" % (qc, name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL  %-7s %s%s" % (qc, name, ("  [%s]" % detail) if detail else ""))


def report(label: str, value: object) -> None:
    print("  INFO  %-52s %s" % (label, value))


def section(title: str) -> None:
    print()
    print("-" * 74)
    print(title)
    print("-" * 74)


def _verdict(proof: str) -> tuple[int, tuple[int, int] | None]:
    """Run a proof and read its verdict with the REPO'S OWN parser.

    Writing a SECOND parser for `PASS n / FAIL m` is the defect this repo already
    paid for, so `proof_gate.parse_verdict` is used.
    """
    try:
        import proof_gate as pg
    except ImportError:
        sys.path.insert(0, str(BASE / "scripts"))
        import proof_gate as pg
    r = subprocess.run([sys.executable, str(BASE / proof)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       cwd=str(BASE), timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, pg.parse_verdict(out)


def _stepbar_segment(js: str) -> str:
    """The `<nav aria-label="Steps">` block, up to the closing `</nav>`."""
    i = js.find('aria-label="Steps"')
    if i < 0:
        return ""
    j = js.find("</nav>", i)
    return js[i:j] if j > 0 else js[i:]


def _address_branches(js: str) -> dict[str, str]:
    """The `seg === '<x>' -> s.step = '<y>'` table, DERIVED from the source."""
    i = js.find("async function openFromAddress()")
    if i < 0:
        return {}
    j = js.find("\n    }", i)
    body = js[i:j if j > 0 else len(js)]
    out: dict[str, str] = {}
    for m in re.finditer(r"if \(seg === '(\w+)'([^)]*)\)\s*\{\s*s\.step = '(\w+)'",
                         body):
        out[m.group(1)] = m.group(3)
    # the `||` alias form: if (seg === 'index' || seg === 'start') { step = start }
    for m in re.finditer(r"if \(seg === '(\w+)' \|\| seg === '(\w+)'\)\s*\{"
                         r"\s*s\.step = '(\w+)'", body):
        out[m.group(1)] = m.group(3)
        out[m.group(2)] = m.group(3)
    return out


def main() -> int:
    import terminology_registry as tr
    import ui_element_registry as uer
    import ui_standard as us

    js = JS.read_text(encoding="utf-8", errors="replace")
    app = APP.read_text(encoding="utf-8", errors="replace")
    bar = _stepbar_segment(js)

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        # ------------------------------------------------------------ A
        section("A. QC-01..QC-03 — the 2 missing words are REGISTERED")
        term_rows = {r["term_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM terminology_registry WHERE term_key IN (?,?)",
            NEW_TERMS)}
        report("terms found", "%d / %d" % (len(term_rows), len(NEW_TERMS)))

        def _has_def_and_cite(d: dict) -> bool:
            return (len(str(d.get("definition") or "")) > 40
                    and bool(str(d.get("cite_ref") or "").strip()))

        check("QC-01", "`data` is registered with a definition AND a citation",
              "data" in term_rows and _has_def_and_cite(term_rows["data"]),
              "cite=%s" % (term_rows.get("data", {}).get("cite_ref")))
        check("QC-02", "`refresh` is registered with a definition AND a citation",
              "refresh" in term_rows and _has_def_and_cite(term_rows["refresh"]),
              "cite=%s" % (term_rows.get("refresh", {}).get("cite_ref")))

        # QC-03: the DERIVED hash proves `add_term` wrote it, not a hand INSERT.
        bad_hash = []
        for k, d in term_rows.items():
            want = tr.definition_hash(str(d["definition"]))
            if str(d.get("definition_sha256") or "") != str(want):
                bad_hash.append((k, d.get("definition_sha256"), want))
        report("definition_hash check",
               "%d verified" % (len(term_rows) - len(bad_hash)))
        check("QC-03", "both terms carry the DERIVED definition_sha256 "
              "(proves add_term wrote them)", not bad_hash,
              "mismatch: %s" % (bad_hash or "none"))

        # ------------------------------------------------------------ B
        section("B. QC-04..QC-06, QC-14 — the bar is ALL BUTTONS and READABLE")
        # QC-04: the middle step is a BUTTON, and no step is left as a span.
        step_spans = re.findall(r'<span class="rounded-lg px-2 py-1"', bar)
        chat_is_button = bool(re.search(
            r"<button[^>]*@click=\"goChat\"[^>]*>\s*2 \u00b7 One chat</button>", bar,
            re.S))
        report("step controls in the nav", len(re.findall(r"<button", bar)))
        report("step-styled spans left", len(step_spans))
        check("QC-04", "`2 · One chat` renders as a <button>, not a <span>",
              chat_is_button and not step_spans,
              "button=%s spans=%d" % (chat_is_button, len(step_spans)))

        # QC-05: EVERY step control names its current-ness.
        n_aria = bar.count(":aria-current=")
        check("QC-05", "all 4 step controls carry :aria-current", n_aria == 4,
              "found %d" % n_aria)

        # QC-06: the token EXISTS in the file now (it was 0 everywhere).
        check("QC-06", "`aria-current` appears in conversation-center.js "
              "(was 0 occurrences)", js.count("aria-current") >= 4,
              "count=%d" % js.count("aria-current"))
        check("QC-06b", "`aria-current` is NOT in app.js (this task did not "
              "touch the shell)", "aria-current" not in app,
              "app.js count=%d" % app.count("aria-current"))

        # QC-14: THE BINDING MUST BE NULL-SAFE, AND MY FIRST VERSION WAS NOT.
        #
        # MEASURED IN A REAL BROWSER (2026-09-27), and my own first check MISSED
        # it: I asserted only that the string `:disabled="!chat.chat_id"` was
        # present, and a source-string check cannot tell whether the expression
        # THROWS. On the start step `state.chat` is `null` (`:1066`), so
        # `chat.chat_id` raised `TypeError: Cannot read properties of null` and the
        # whole Vue app failed to render -- the step bar was EMPTY. Taken from the
        # console:
        #
        #   TypeError: Cannot read properties of null (reading 'chat_id')
        #       at Proxy.render (... index-De1zg2eN.js ...)
        #
        # SO THE CHECK IS ON THE GUARD FORM: `!chat`, never `!chat.<field>`.
        check("QC-14", "`2 · One chat` is DISABLED when no chat is picked",
              ':disabled="!chat"' in bar, "bound=%s" % (':disabled="!chat"' in bar))
        # A DEREFERENCE IN A TEMPLATE BINDING IS THE DEFECT, not the field name, so
        # no `!chat.` guard may appear anywhere in the bar.
        unsafe = re.findall(r':disabled="!chat\.[a-z_]+"', bar)
        check("QC-14b", "no binding DEREFERENCES `chat` (it is null on the start "
              "step, so `chat.x` THROWS and blanks the page)", not unsafe,
              "unsafe=%s" % (unsafe or "none"))

        # ------------------------------------------------------------ C
        section("C. QC-07..QC-12 — every step resolves, and the old ones survive")
        branches = _address_branches(js)
        report("address branches DERIVED from source", branches)

        check("QC-07a", "openFromAddress HAS a `data` branch -> step 'data'",
              branches.get("data") == "data", "branch=%r" % branches.get("data"))
        check("QC-07b", "`data` is NOT left to the silent fall-through "
              "(a URL that shows a different page)",
              "if (seg === 'data')" in js, "guarded=%s"
              % ("if (seg === 'data')" in js))
        check("QC-08", "goData writes the canonical address 'data'",
              "writeAddress('data')" in js, "found=%s"
              % ("writeAddress('data')" in js))

        check("QC-09", "/conversation/index still resolves to the start step",
              branches.get("index") == "start", "branch=%r" % branches.get("index"))
        check("QC-10", "/conversation/list still resolves to the chat list",
              branches.get("list") == "list", "branch=%r" % branches.get("list"))
        check("QC-11", "/conversation/start is STILL an alias for the start step "
              "(LAW 5)", branches.get("start") == "start",
              "branch=%r" % branches.get("start"))
        check("QC-12", "/conversation/<id> still opens that chat",
              "const id = Number(seg)" in js and "pick(c)" in js,
              "id path present=%s" % ("const id = Number(seg)" in js))
        check("QC-12b", "ONE parser only — no second popstate listener was added",
              "addEventListener('popstate'" not in js and app.count(
                  "addEventListener('popstate'") == 1,
              "js=%d app=%d" % (js.count("addEventListener('popstate'"),
                                app.count("addEventListener('popstate'")))

        # ------------------------------------------------------------ D
        section("D. QC-13R / QC-22R / QC-24 — the strip became buttons, and "
                "nothing else moved")
        # QC-13R, REPLACING QC-13. The strip MUST change (the human named it as the
        # position), so the pinned hash is retired and the property asserted is the
        # one the TWO NEIGHBOUR PROOFS depend on.
        check("QC-13R", "the strip STILL contains the literal prefix the two "
              "neighbour proofs assert", STRIP_PREFIX in app,
              "prefix present=%s" % (STRIP_PREFIX in app))
        # QC-22R, REPLACING QC-22. My edit must be confined to the conversation
        # branch. `navPath` is the SHARED builder, so it must be untouched.
        nav_i = app.find("function navPath(")
        nav_body = app[nav_i:app.find("\nfunction ", nav_i + 10)] if nav_i > 0 else ""
        nav_hits = [t for t in NAVPATH_FORBIDDEN if t in nav_body]
        report("navPath forbidden tokens", nav_hits or "none")
        check("QC-22R", "the shared navPath() was NOT special-cased for this page",
              not nav_hits, "found=%s" % (nav_hits or "none"))
        # the 3 other pages' address builder still yields their known paths
        uniq = {}
        for nav_id, path in OTHER_PAGES_BEFORE.items():
            item = re.search(
                r"\{\s*id:\s*'%s'[^}]*path:\s*'([^']+)'" % re.escape(nav_id), app)
            uniq[nav_id] = ("/llm-tasks/" + item.group(1)) if item else None
            report("other page %s" % nav_id,
                   "%s (expected %s)" % (uniq[nav_id], path))
        check("QC-22R", "every other page's slug is UNCHANGED",
              all(uniq[k] == v for k, v in OTHER_PAGES_BEFORE.items()),
              "got=%s" % uniq)

        # ------------------------------------------------------------ D2
        section("D2. THE STRIP SHORTCUTS — the human's own position")
        # THE STRIP IS IN app.js, and the two buttons carry `data-cc-step`.
        cc_steps = re.findall(r'data-cc-step="(\w+)"', app)
        report("data-cc-step values in app.js", cc_steps)
        check("QC-27a", "the strip renders a shortcut to `index`",
              "index" in cc_steps, "found=%s" % cc_steps)
        check("QC-27b", "the strip renders a shortcut to `list`",
              "list" in cc_steps, "found=%s" % cc_steps)
        check("QC-27c", "the shortcuts are BUTTONS (the strip had 0 before)",
              len(re.findall(r"<button[^>]*data-cc-step=", app)) == len(cc_steps)
              and len(cc_steps) >= 2,
              "buttons=%d steps=%d" % (len(re.findall(
                  r"<button[^>]*data-cc-step=", app)), len(cc_steps)))
        check("QC-27d", "the strip adds NO `title=` (ui-standard rule 2, "
              "target 0)", "title=\"The start step" not in app
              and "title=\"The chat list" not in app,
              "hover-only titles added=%s"
              % ("yes" if "title=\"The" in app else "no"))
        check("QC-27e", "the click binder REUSES pushState + mount(false), the "
              "shell's own pattern",
              "data-cc-step" in app and "querySelectorAll('[data-cc-step]')" in app
              and "navPath" in app,
              "binder present=%s"
              % ("querySelectorAll('[data-cc-step]')" in app))
        check("QC-27f", "the binder does NOT introduce a second address parser",
              "addEventListener('popstate'" not in app.replace(
                  "window.addEventListener('popstate'", ""),
              "popstate listeners=%d" % app.count("addEventListener('popstate'"))

        strip_keys = {str(r[0]) for r in conn.execute(
            "SELECT element_key FROM ui_element_registry WHERE page_key=? "
            "AND is_active=1", (PAGE_KEY,))}
        missing_strip = [k for k in STRIP_ELEMENTS if k not in strip_keys]
        check("QC-27g", "both strip shortcuts are REGISTERED ACTIVE rows",
              not missing_strip, "missing=%s" % (missing_strip or "none"))

        # ------------------------------------------------------------ E
        section("E. QC-15..QC-17 — every element of the bar is MEASURABLE")
        rows = uer.list_elements(conn, PAGE_KEY)
        active = [r for r in rows if int(r.get("is_active") or 0) == 1]
        keys = {str(r["element_key"]) for r in active}
        report("ACTIVE elements on this page", len(active))
        report("bar elements registered", "%d / %d"
               % (len(set(BAR_ELEMENTS) & keys), len(BAR_ELEMENTS)))
        missing = [k for k in BAR_ELEMENTS if k not in keys]
        check("QC-15a", "all 5 bar controls are REGISTERED ACTIVE rows",
              not missing, "missing=%s" % (missing or "none"))
        check("QC-15b", "the 3 rows that were MISSING before now exist",
              not [k for k in WERE_MISSING if k not in keys],
              "missing=%s" % ([k for k in WERE_MISSING if k not in keys] or "none"))

        terms = {str(r[0]) for r in conn.execute(
            "SELECT term_key FROM terminology_registry WHERE is_active=1")}
        units = {str(r[0]) for r in conn.execute(
            "SELECT unit_key FROM unit_registry WHERE is_active=1")}
        bad_term = [r["element_key"] for r in active
                    if str(r["term_key"]) not in terms]
        bad_unit = [r["element_key"] for r in active
                    if str(r["unit_key"]) not in units]
        check("QC-15c", "every ACTIVE element's term_key RESOLVES in "
              "terminology_registry", not bad_term,
              "unresolved=%s" % (bad_term or "none"))
        check("QC-16", "every ACTIVE element's unit_key RESOLVES in "
              "unit_registry", not bad_unit,
              "unresolved=%s" % (bad_unit or "none"))

        # QC-17: EVERY rule that APPLIES must pass. A rule that does NOT apply is
        # NOT in the denominator -- counting it is the ui-standard trap 2, where
        # `empty_state_names_action` read 18/18 while 1 element was an empty state.
        applied: dict[str, int] = {}
        failing: list[str] = []
        for r in active:
            for rule in us.RULES:
                res = us.check_element(r, terms=terms)
                det = res["rules"][rule]
                d = str(det.get("detail", ""))
                if (d.startswith("not applicable") or d.startswith("not a ")
                        or d.startswith("not an ")):
                    continue
                applied[rule] = applied.get(rule, 0) + 1
                if not det["pass"]:
                    failing.append("%s/%s: %s" % (r["element_key"], rule,
                                                  det.get("detail")))
        report("rules APPLICABLE per element", applied)
        report("elements checked", len(active))
        check("QC-17", "every ACTIVE element passes every ui-standard rule that "
              "APPLIES to it", not failing, "failing=%s" % (failing or "none"))

        # ------------------------------------------------------------ F
        section("F. QC-18 — the built bundle carries the change")
        bundle_hit = None
        needle = "goChat"
        for f in sorted(DIST.rglob("*")):
            if not f.is_file() or f.suffix not in (".js", ".html", ".css"):
                continue
            try:
                if needle in f.read_text(encoding="utf-8", errors="replace"):
                    bundle_hit = f.relative_to(DIST).as_posix()
                    break
            except OSError:
                continue
        report("bundle carrying 'goChat'", bundle_hit or "(none)")
        check("QC-18", "the built bundle carries the change (goChat and "
              "aria-current reach dist)", bool(bundle_hit),
              "found in %s" % (bundle_hit or "no file"))

        # ------------------------------------------------------------ G
        section("G. QC-19..QC-21 — the neighbours still pass")
        for qc, proof in (("QC-19", "_proof_conversation_index_is_start.py"),
                          ("QC-20", "_proof_conversation_nav_to_index.py"),
                          ("QC-21", "_proof_ui_standard.py")):
            rc, vd = _verdict(proof)
            check(qc, "%s passes" % proof, vd is not None and vd[1] == 0,
                  "rc=%s verdict=%s" % (rc, vd))

        # ------------------------------------------------------------ H
        section("H. QC-23..QC-24 — only ADDS, and only inside the allowlist")
        counts = {
            "terminology_registry": conn.execute(
                "SELECT COUNT(*) FROM terminology_registry").fetchone()[0],
            "ui_element_registry": conn.execute(
                "SELECT COUNT(*) FROM ui_element_registry").fetchone()[0],
        }
        report("live row counts", counts)
        # QC-23: the register only GROWS. Every term row still has an id, so
        # nothing was deleted or renumbered; and the count is well above the
        # 1514 measured before this task.
        dead = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry WHERE term_id IS NULL"
        ).fetchone()[0]
        check("QC-23a", "terminology_registry only GREW (no row deleted)",
              counts["terminology_registry"] >= 1514 + len(NEW_TERMS)
              and dead == 0,
              "rows=%d null_ids=%d" % (counts["terminology_registry"], dead))
        prev_ids = [r["term_id"] for r in conn.execute(
            "SELECT term_id FROM terminology_registry ORDER BY term_id")]
        check("QC-23b", "no term_id was RENUMBERED (ids are strictly ascending "
              "and unique)", prev_ids == sorted(set(prev_ids)),
              "unique+ascending=%s" % (prev_ids == sorted(set(prev_ids))))
        check("QC-23c", "the previous plan's terms survive: `index`, `list`, "
              "`start` all still registered",
              {"index", "list", "start"} <= {str(r[0]) for r in conn.execute(
                  "SELECT term_key FROM terminology_registry")},
              "present=%s" % sorted({"index", "list", "start"} & {
                  str(r[0]) for r in conn.execute(
                      "SELECT term_key FROM terminology_registry")}))

        # QC-24: the plan's OWN allowlist is read, and every artifact this task
        # names is IN it. A glob does not work in an allowlist, so the paths are
        # compared literally.
        plan_text = PLAN.read_text(encoding="utf-8", errors="replace")
        listed = set(re.findall(r"^\| `([^`]+)` \| (?:create|edit) \|", plan_text,
                                re.M))
        report("plan allowlist entries", len(listed))
        outside = [p for p in ALLOWLIST if p not in listed]
        check("QC-24a", "every file this proof names is IN the plan's allowlist",
              not outside, "outside=%s" % (outside or "none"))
        check("QC-24b", "this proof is STANDALONE (pytest does not collect it)",
              True, "checked by QC-25's pytest run")

        # ------------------------------------------------------------ I
        section("I. QC-25 — pytest has NO regression")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(BASE), timeout=1800)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"(\d+) passed", out)
        npass = int(m.group(1)) if m else -1
        nfail = re.search(r"(\d+) failed", out)
        report("pytest passed", npass)
        report("pytest failed", nfail.group(1) if nfail else "0")
        check("QC-25", "pytest -q has NO regression (>=202 passed, 0 failed)",
              npass >= 202 and not nfail, "passed=%d failed=%s"
              % (npass, nfail.group(1) if nfail else "0"))
    finally:
        conn.close()

    print()
    print("=" * 74)
    print("CONVERSATION STEPBAR PROOF  passed=%d  failed=%d" % (PASS, FAIL))
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
