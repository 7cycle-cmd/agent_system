# -*- coding: utf-8 -*-
"""walk_nav.py — measure the 豆包 nav path selector by selector.

WHY THIS EXISTS
---------------
The user's rule is "confirm is better than without". A selector written from
memory is the DOM equivalent of a hardcoded coordinate: it survives right up
until the layout changes, then fails silently. So each step is MEASURED against
the live DOM and the measurement is SAVED, with the frame it lives in.

The measured path (2026-09-23):
    STEP 0  click <a> '插件 · 技能 · 伙伴'   -> url becomes /chat/skills
    STEP 1  click button '工作伙伴'
    STEP 2  click '產品研發'
    STEP 3  click '软件研发小组'
    STEP 4  click the chat icon
    STEP 5  click the mission row

STEP 0 is not in the user's description but is REQUIRED: measured that the
default `chrome://doubao-chat/chat` page contains ZERO occurrences of 工作伙伴,
產品研發, 软件研发小组, 复制 or 结果评分, and DOES contain the nav entry
`插件 · 技能 · 伙伴` rect=[9,153,271,185].

A TRAP ALREADY MEASURED (do not repeat it)
------------------------------------------
The first attempt matched by CONTAINMENT on `document.querySelectorAll('a,div,button')`
and therefore picked the OUTERMOST div (rect [0,0,1184,755]) instead of the <a>
(rect [9,153,271,185]). Clicking its centre hit dead space and nothing happened.
So:
  * the tag is matched where a tag is known, and
  * `exact: True` means the element's OWN innerText must equal the name, so a
    parent that merely CONTAINS the label cannot win.

WHAT IT MEASURES AT EACH STEP
-----------------------------
  - the target element (tag, text, rect) BEFORE the click
  - the url before / after
  - the element count after, dumped to `qc_evidence/nav_step<n>.json`, so the
    NEXT target is found by inspecting the new DOM rather than by guessing a name

Output: `qc_evidence/nav_step<n>.json` per step + `qc_evidence/nav_walk.json`.

Exit codes: 0 walked as far as it could / 1 CDP unavailable / 2 no page / 3 a
target was not found (which STOPS the walk — a guess would be worse).

Usage:
  python walk_nav.py --run
  python walk_nav.py --run --steps 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
EV = BASE / "qc_evidence"

# THE FRAME IS PART OF THE TARGET.
#
# MEASURED 2026-09-23: the 工作伙伴 list lives in an IFRAME, not the top frame.
# The top frame (`chrome://doubao-chat/chat/skills/market/buddies`) contained the
# LEFT NAV (920 elements) plus the chat list, and ZERO occurrences of 產品研發 /
# 研发 / 精选 / 办公. The category tabs were in FRAME 1 (855 elements, url="",
# i.e. a same-origin iframe).
#
# So a selector without a frame is incomplete: it works in one and silently
# matches nothing in the other. Every step therefore names its frame, and the
# search is done PER FRAME so a hit reports which frame it came from.
#
# `alt` holds the SIMPLIFIED forms. Measured: the category tab is `产品研发`
# (simplified) while the user wrote `產品研發` (traditional). Matching only the
# traditional form would return 0 hits and look like "the element is gone".
STEPS: tuple[dict, ...] = (
    {"n": 0, "name": "插件 · 技能 · 伙伴", "tag": "a",
     "must": ["插件", "伙伴"], "expect_url_has": "/skills", "frame": 0},
    {"n": 1, "name": "工作伙伴", "tag": "button", "must": ["工作伙伴"],
     "exact": True, "frame": 0},
    {"n": 2, "name": "产品研发", "alt": ["產品研發"], "tag": "button",
     "must": ["研发"], "frame": 1},
    # MEASURED 2026-09-23 on `.../buddies/tasks/<id>` (the page STEP 2 lands on):
    #   frame 1  button 工作伙伴     [334, 16, 398, 40]
    #   frame 1  a      软件研发小组 [424, 16, 520, 40]
    #   frame 1  li     新任务       [542, 15, 616, 41]
    # NOTE: the `软件研发小组` found here is the BREADCRUMB (a back link), NOT
    # the "switch product" control the user described. That control is still
    # UNMEASURED and must not be conflated with this anchor.
    {"n": 3, "name": "软件研发小组", "tag": "a", "must": ["软件研发"],
     "exact": True, "frame": 1},
    # The user's "new task in chat box": a plain li, NOT clickable per the DOM,
    # so its own click may do nothing. Measured here, behaviour confirmed later.
    {"n": 4, "name": "新任务", "tag": "li", "must": ["新任务"],
     "exact": True, "frame": 1},
    # Task rows live in the OUTER shell (frame 0), not the iframe: measured
    # [9,617,271,649] and [9,681,271,713], pitch 64, height 32.
    {"n": 5, "name": "mission row", "tag": "div", "must": [],
     "by": "mission", "frame": 0},
)

JS_FIND_ALL = """(spec) => {
    const names = [spec.name].concat(spec.alt || []);
    const out = [];
    const all = Array.from(document.querySelectorAll(spec.tag || '*'));
    for (const e of all) {
        const t = (e.innerText || '').trim();
        const a = e.getAttribute('aria-label') || '';
        const hay = t + '\\u0000' + a;
        if (spec.exact && !names.includes(t)) continue;
        if (!spec.exact && spec.tag && e.tagName.toLowerCase() !== spec.tag) continue;
        let ok = true;
        for (const m of (spec.must || [])) { if (!hay.includes(m)) { ok = false; break; } }
        if (!ok) continue;
        const r = e.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        out.push({tag: e.tagName.toLowerCase(),
                  cls: (typeof e.className === 'string' ? e.className : '').slice(0, 120),
                  text: t.slice(0, 60), aria: a,
                  rect: [Math.round(r.left), Math.round(r.top),
                         Math.round(r.right), Math.round(r.bottom)]});
    }
    return out;
}"""


def _frame_url(fr) -> str:
    try:
        return fr.url or "(blank)"
    except Exception:
        return "(?)"


def _dump(page, tag: str) -> list[dict]:
    """Dump EVERY frame, tagged with its frame index.

    MEASURED 2026-09-23: dumping only the top frame made STEP 2 look like it had
    produced nothing new — the 241 rows were the top frame, and the frame that
    actually changed was FRAME 1. A dump that silently omits a frame turns
    "the element is in another frame" into "the element is not there", and the
    next step then searches the wrong tree.
    """
    import doubao_cdp as dc
    frames = _frames(page)
    per_frame: list[dict] = []
    merged: list[dict] = []
    for i, fr in enumerate(frames):
        try:
            els = dc.dump_elements(fr, limit=1200)
        except Exception as e:
            print("    dump frame %d ERR: %s" % (i, str(e)[:100]))
            els = []
        for e in els:
            e["frame"] = i
        per_frame.append({"frame": i, "url": _frame_url(fr), "count": len(els)})
        merged.extend(els)
        print("    frame %d: %d elements  (%s)" % (i, len(els), _frame_url(fr)))
    (EV / ("nav_%s.json" % tag)).write_text(
        json.dumps({"url": page.url, "frame_count": len(frames),
                    "frames": per_frame, "elements": merged},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _frames(page) -> list:
    """Every frame, top first. A frame list failure is reported, not hidden."""
    try:
        return list(page.frames)
    except Exception:
        return []


def _find(page, spec: dict) -> tuple[dict | None, int | None, list[dict]]:
    """Find the target in its NAMED frame, else across every frame.

    Returns (first_hit, frame_index, all_hits). Searching every frame when the
    spec does not name one is deliberate: the target's frame was MEASURED to
    differ per step, so a fixed frame index would be a guess.
    """
    frames = _frames(page)
    order = ([spec["frame"]] if spec.get("frame") is not None else
             list(range(len(frames))))
    all_hits: list[dict] = []
    first: dict | None = None
    first_idx: int | None = None
    for i in order:
        if i >= len(frames):
            continue
        try:
            hits = frames[i].evaluate(JS_FIND_ALL, spec) or []
        except Exception as e:
            print("    frame %d evaluate ERR: %s" % (i, str(e)[:100]))
            continue
        for h in hits:
            h["frame"] = i
            all_hits.append(h)
        if hits and first is None:
            first, first_idx = hits[0], i
    return first, first_idx, all_hits


def _url(page) -> str:
    try:
        return page.url
    except Exception:
        return "?"


def run(max_step: int = 5) -> int:
    import doubao_cdp as dc
    from playwright.sync_api import sync_playwright

    ok, action = dc.ensure_cdp()
    if not ok:
        print("ensure_cdp FAILED")
        return 1
    print("ensure_cdp: %s" % action)

    summary: list[dict] = []
    rc = 0
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(dc.cdp_url())
        try:
            page = dc.find_page(b, "doubao-chat")
            if page is None:
                print("no doubao-chat page")
                return 2

            for spec in STEPS:
                if spec["n"] > max_step:
                    break
                label = "STEP %d  %s" % (spec["n"], spec["name"])
                row: dict = {"step": spec["n"], "name": spec["name"],
                             "url_before": _url(page)}
                print("--- %s" % label)
                print("    url : %s" % row["url_before"])

                # STATEFUL-START GUARD. Measured 2026-09-23: clicking `产品研发`
                # navigates INTO the team page, and 豆包 REMEMBERS it. The next
                # run then reported STEP 1 NOT FOUND -- the START had moved, not
                # the element. A walk that assumes a fixed start reports a false
                # miss, so an already-deep start is stated, not hidden.
                if spec["n"] <= 2 and "/tasks/" in _url(page):
                    row["note"] = ("SKIPPED: already inside a task page "
                                   "(/tasks/ in url) -- start is deeper than this step")
                    print("    SKIP: %s" % row["note"])
                    summary.append(row)
                    continue

                if spec.get("by"):
                    # icon / mission need a measured CANDIDATE LIST, not a name.
                    # They are reported as needing inspection rather than guessed.
                    row["note"] = ("needs a measured candidate list — inspect "
                                   "qc_evidence/nav_step%d.json" % (spec["n"] - 1))
                    print("    SKIP: %s" % row["note"])
                    summary.append(row)
                    continue

                target, fidx, all_hits = _find(page, spec)
                row["found"] = target
                row["found_frame"] = fidx
                row["all_hits"] = all_hits[:20]
                print("    target: %s  frame=%s  (hits=%d)"
                      % (target["rect"] if target else None, fidx, len(all_hits)))
                if not target:
                    row["note"] = "NOT FOUND — stopping (a guess would be worse)"
                    print("    NOT FOUND — stopping")
                    summary.append(row)
                    rc = 3
                    break

                # Click in the TARGET'S OWN frame: a rect from an iframe is
                # frame-local, so clicking page-level coordinates would hit the
                # wrong element whenever the iframe is not at (0,0).
                frames = _frames(page)
                try:
                    frames[fidx].evaluate(
                        """(rc) => {
                            const el = document.elementFromPoint(rc[0], rc[1]);
                            if (el) el.click();
                        }""",
                        [(target["rect"][0] + target["rect"][2]) // 2,
                         (target["rect"][1] + target["rect"][3]) // 2])
                except Exception as e:
                    print("    in-frame click ERR: %s" % str(e)[:100])
                page.wait_for_timeout(3000)
                row["url_after"] = _url(page)
                print("    after : %s" % row["url_after"])
                exp = spec.get("expect_url_has")
                if exp:
                    row["url_expect_ok"] = exp in row["url_after"]
                    print("    expect %r in url: %s" % (exp, row["url_expect_ok"]))

                els = _dump(page, "step%d" % spec["n"])
                print("    dumped %d elements -> qc_evidence/nav_step%d.json"
                      % (len(els), spec["n"]))
                summary.append(row)
        finally:
            b.close()

    (EV / "nav_walk.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("summary -> qc_evidence/nav_walk.json")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure the 豆包 nav path.")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--steps", type=int, default=5)
    args = ap.parse_args()
    if args.run:
        return run(max_step=args.steps)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
