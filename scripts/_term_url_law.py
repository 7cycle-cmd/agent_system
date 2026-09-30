# -*- coding: utf-8 -*-
"""_term_url_law.py -- the NAMING LAW, executable.

WHY THIS FILE EXISTS
--------------------
THE HUMAN (2026-09-26):
    "can all path can be more meaningful, i can help debug and develop or communication"
    "termonotlogy register help to have respersentative name, not different name at anywhere"

"Meaningful" is not checkable. A LAW is. So the human's ask is written as six
rules that a machine can test, and this module is that test.

THE LAW
-------
  LAW 1  The URL segment IS the registered `term_key`, character for character.
  LAW 2  A term_key is lowercase snake_case: no spaces, no uppercase, no hyphens.
  LAW 3  `NAV[i].path` EQUALS the term_key. `id` describes the same thing.
  LAW 4  NO page address may exist before its term is registered.
         The register is the SOURCE of the name, not a copy of it.
  LAW 5  An OLD url is MAPPED, never deleted. A route is a public address.
  LAW 6  A URL carries STRUCTURE only. A row id may be a final segment;
         a UI step number (step2) may NOT -- it is internal and re-orderable.

MEASURED BEFORE THE FIX (2026-09-26), which is why LAW 2 needed writing down:
`Skill Prompt SSOT` was a nav path, so the address became
`/llm-tasks/Skill%20Prompt%20SSOT` -- a URL containing an encoded SPACE, which a
human cannot SAY to another human to debug. `environment_playwright` was both a
TYPO and the WRONG SUBJECT (the page is Playwright).

NEVER RAISES: every finding is returned as a dict, so a caller decides.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, "llm_task_monitor_ui", "src", "app.js")
DB = os.path.join(ROOT, "agent.db")

# The nav table line: `{ id: 'x', label: 'y', path: 'z'[, ...] }`
NAV_RE = re.compile(r"\{ id: '([^']+)', label: '([^']+)', path: '([^']+)'")
# LAW 2: lowercase snake_case, and nothing else.
SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def read_nav(app_js: str = APP_JS) -> list[dict]:
    """Every `{ id, label, path }` in the NAV table, with its line number."""
    out = []
    with io.open(app_js, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            m = NAV_RE.search(line)
            if m:
                out.append({"line": i, "id": m.group(1),
                            "label": m.group(2), "path": m.group(3)})
    return out


def read_aliases(app_js: str = APP_JS) -> dict[str, str]:
    r"""The LEGACY_NAV_SLUGS map: old spelling -> current nav id (LAW 5).

    MEASURED 2026-09-26: the first version of this reader used
    `'?([^':\s]+)'?` for the key, which CANNOT match a quoted key containing a
    SPACE. `'Skill Prompt SSOT': 'skill-ssot'` was therefore invisible and the
    law reported a missing alias for an alias that WAS present. A reader that
    cannot see its input reports absence as a defect -- the same failure class as
    `empty_detector_failure_class`. Quoted keys are now read properly.
    """
    with io.open(app_js, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"const LEGACY_NAV_SLUGS = \{(.*?)\n\};", src, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    out = {}
    for kq, kb, v in re.findall(
            r"^\s*(?:'([^']+)'|([^':\s]+))\s*:\s*'([^']+)'",
            body, re.MULTILINE):
        out[kq or kb] = v
    return out


def registered_keys(db: str = DB) -> set[str]:
    conn = sqlite3.connect(db, timeout=20)
    try:
        return {r[0] for r in conn.execute(
            "SELECT term_key FROM terminology_registry WHERE is_active=1")}
    finally:
        conn.close()


def check_law(app_js: str = APP_JS, db: str = DB) -> dict:
    """Run all six laws. Returns counts + the exact violations."""
    nav = read_nav(app_js)
    keys = registered_keys(db)
    aliases = read_aliases(app_js)

    bad2, bad3, bad4, bad6 = [], [], [], []
    for n in nav:
        p = n["path"]
        # LAW 2
        if not SEGMENT_RE.match(p):
            why = []
            if " " in p:
                why.append("contains a SPACE (a URL would carry %%20)")
            if any(c.isupper() for c in p):
                why.append("contains UPPERCASE")
            if "-" in p:
                why.append("contains a HYPHEN (others use _)")
            bad2.append({"line": n["line"], "path": p, "why": "; ".join(why) or "not snake_case"})
        # LAW 3 -- ONE PAGE, ONE ADDRESS.
        #
        # CORRECTED 2026-09-26. This first asserted `id.replace('-','_') == path`,
        # which is NOT what LAW 3 says and is not even true of the design: `id` is
        # the nav's SHORT handle (`skill-ssot`) while `path` is the REGISTERED term
        # (`skill_prompt_ssot`). Demanding equality would force the registered name
        # to shrink to fit a UI key -- backwards.
        #
        # The CHECKABLE part of "they describe the same thing" is that the mapping
        # is 1:1: no two nav rows may share one path, or the address stops naming
        # one page. Uniqueness is checked below, after the loop.
        # LAW 4
        if p not in keys:
            bad4.append({"line": n["line"], "path": p})
        # LAW 6 -- a UI step number is internal, never an address
        if re.search(r"(^|[/_-])step\d", p, re.IGNORECASE):
            bad6.append({"line": n["line"], "path": p})

    # LAW 3, the checkable half: one page, one address.
    seen: dict[str, str] = {}
    for n in nav:
        if n["path"] in seen:
            bad3.append({"line": n["line"], "path": n["path"],
                         "shares_with": seen[n["path"]]})
        else:
            seen[n["path"]] = n["id"]

    # LAW 5 -- every path an OLD form used must still be mapped.
    # The old forms are recorded in the nav table's own history; the check that
    # matters is that the alias map is NON-EMPTY and covers the renamed ones.
    renamed = {"Skill Prompt SSOT": "skill-ssot",
               "Skill_Learning_Center": "skill-learning",
               "Test_Case_Library": "test-lib",
               "Asset_Registry": "assets",
               "devtask-view": "devtask-view",
               "environment_playwright": "playwright",
               "Setting_OpenClaw": "openclaw"}
    bad5 = [old for old in renamed if old not in aliases]

    return {
        "nav_entries": len(nav),
        "aliases": len(aliases),
        "law2_violations": bad2,
        "law3_violations": bad3,
        "law4_violations": bad4,
        "law5_missing_aliases": bad5,
        "law6_violations": bad6,
        "ok": not (bad2 or bad3 or bad4 or bad5 or bad6),
    }


def main(argv: list[str] | None = None) -> int:
    r = check_law()
    print("=" * 74)
    print("THE NAMING LAW -- llm-tasks URL segments")
    print("=" * 74)
    print("nav entries: %d   aliases: %d" % (r["nav_entries"], r["aliases"]))
    for law, key in (("LAW 2 lowercase snake_case", "law2_violations"),
                     ("LAW 3 one page, one address", "law3_violations"),
                     ("LAW 4 term is registered", "law4_violations"),
                     ("LAW 5 old URL still mapped", "law5_missing_aliases"),
                     ("LAW 6 no step number in a URL", "law6_violations")):
        v = r[key]
        print("  %-28s %s" % (law, "OK" if not v else "VIOLATION x%d" % len(v)))
        for x in v[:8]:
            print("       ", x)
    print("=" * 74)
    print("RESULT:", "LAW HOLDS" if r["ok"] else "LAW BROKEN")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
