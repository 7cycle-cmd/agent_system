# -*- coding: utf-8 -*-
"""auto_rect_audit.py — the rect audit, wired as a GATE rather than a habit.

THE POINT OF THIS FILE
----------------------
The previous step found a rect 233px wrong and fixed it. That was ONE discovery,
found because someone happened to look. A discovery that depends on someone
deciding to look is not a control — it is luck with a paper trail.

So the same three steps (re-locate -> compare -> record) are made automatic and
attached to the paths that can be harmed by a bad rect:

  1. CALLABLE:  `audit_rects()` for any caller
  2. PRE-CLICK:  `assert_rect_ok(target_id)` — raise instead of clicking blind
  3. SCHEDULABLE: `--watch` re-audits on an interval and appends findings

WHY A GATE AND NOT A REPORT
---------------------------
Playwright re-checks before every action and raises on failure; Selenium raises
StaleElementReferenceException and documents "always relocate the element every
time you go to use it". Both fail rather than proceed. A periodic report that
nobody reads is the failure mode this replaces.

DESIGN
------
* `not_applicable` is a real outcome: a rect inside a CLOSED popup cannot be
  judged. Reporting it as a mismatch (as the first version did, 3 false alarms)
  trains the reader to ignore the tool.
* Every finding is written to `skill_lesson`, so it is queryable rather than
  prose.
* A finding is only written for a JUDGED rect, never for a skipped one.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")

AGENT_DB = Path(r"C:\projects\agent_system\agent.db")
COORD_DB = Path(r"C:\projects\agent_system\coords.db")
FINDINGS = Path(r"C:\projects\agent_system\rect_audit_findings.json")


class RectNotProven(RuntimeError):
    """Raised instead of clicking a rect that is not proven to be on target."""


def _audit_module():
    import importlib
    import _self_audit_rects as m
    importlib.reload(m)
    return m


def audit_rects(*, apply_fixes: bool = False, verbose: bool = False) -> dict:
    """Re-locate every registered rect by CONTENT and report/repair mismatches.

    Returns {ok, judged, mismatched, not_applicable, findings[], corrected}.
    """
    m = _audit_module()
    import f_perm_click as p
    from PIL import Image

    fg = p._foreground_info()
    if not fg.get("is_code"):
        return {"ok": False, "error": "not_foreground",
                "foreground": fg.get("process"),
                "note": "VS Code must be in front for a rect audit"}

    conn = sqlite3.connect(str(COORD_DB))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT target_id, popup_id, x1, y1, x2, y2, label, checklist_confirm "
        "FROM target_area ORDER BY target_id")]
    conn.close()
    if not rows:
        return {"ok": True, "judged": 0, "mismatched": 0,
                "not_applicable": 0, "findings": [], "corrected": 0}

    shot = p._screenshot("auto_audit")
    prov = p._screenshot_provenance(shot)
    iw, ih = prov.get("width"), prov.get("height")
    screen = p._screen_size()
    sx = screen[0] / float(iw)
    im = Image.open(shot).convert("RGB")

    open_now = False
    try:
        open_now = bool(p._picker_present(shot, prov).get("ok"))
    except Exception:
        open_now = False

    findings, judged, skipped, corrected = [], 0, 0, 0
    for row in rows:
        res = m.audit_one(im, iw, ih, sx, row, argparse.Namespace(apply=apply_fixes),
                          container_open=open_now)
        if not res["applicable"]:
            skipped += 1
            continue
        judged += 1
        x1, y1, x2, y2 = res["stored"]
        if not res["found"]:
            findings.append({"target_id": row["target_id"], "kind": "not_found",
                             "stored": list(res["stored"])})
            continue
        a, b, txt = res["found"]
        ry1, ry2 = int(a * sx), int(b * sx)
        if ry2 < y1 or ry1 > y2:
            gap = min(abs(y1 - ry2), abs(ry1 - y2))
            findings.append({"target_id": row["target_id"], "kind": "mismatch",
                             "stored": [x1, y1, x2, y2],
                             "located": [ry1, ry2], "gap_px": gap,
                             "via": txt[:60]})
            if apply_fixes:
                pad = 6
                c = sqlite3.connect(str(COORD_DB))
                c.execute("UPDATE target_area SET y1=?, y2=?, checklist_confirm='no',"
                          " updated_at=CURRENT_TIMESTAMP WHERE target_id=?",
                          (ry1 - pad, ry2 + pad, row["target_id"]))
                c.commit()
                c.close()
                corrected += 1

    if findings and apply_fixes:
        _emit_lessons(findings)

    out = {"ok": True, "judged": judged,
           "mismatched": len([f for f in findings if f["kind"] == "mismatch"]),
           "not_found": len([f for f in findings if f["kind"] == "not_found"]),
           "not_applicable": skipped, "findings": findings,
           "corrected": corrected, "menu_open": open_now,
           "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _append_findings(out)
    if verbose:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _emit_lessons(findings: list[dict]) -> None:
    import skill_learning as sl
    for f in findings:
        if f["kind"] != "mismatch":
            continue
        ref = "auto-audit:mismatch:%s:%d" % (f["target_id"], f["stored"][1])
        c = sqlite3.connect(str(AGENT_DB))
        c.row_factory = sqlite3.Row
        seen = c.execute("SELECT 1 FROM skill_lesson WHERE source_ref=?",
                         (ref,)).fetchone()
        c.close()
        if seen:
            continue
        sl.add_lesson(
            "env_task_proof",
            ("Auto-audit found rect %s %dpx from the target it names. A rect is "
             "stored once and re-used, so an error survives indefinitely unless "
             "something re-checks it." % (f["target_id"], f["gap_px"])),
            source_type="self_fail",
            root_cause="rect re-used without re-verification against the live screen",
            suggested_fix=("re-locate by content before every click and REFUSE "
                           "while checklist_confirm != 'yes'"),
            source_ref=ref, status="draft", db_path=AGENT_DB)


def _append_findings(out: dict) -> None:
    hist = []
    if FINDINGS.is_file():
        try:
            hist = json.loads(FINDINGS.read_text(encoding="utf-8"))
        except Exception:
            hist = []
    hist.append(out)
    FINDINGS.write_text(json.dumps(hist[-200:], ensure_ascii=False, indent=2),
                        encoding="utf-8")


def assert_rect_ok(target_id: str) -> None:
    """Raise RectNotProven unless this rect is VERIFIED before use.

    The pre-action gate. Call this immediately before any click that uses a
    stored rect; it refuses rather than letting the click proceed blind — the
    behaviour Playwright and Selenium both take.
    """
    conn = sqlite3.connect(str(COORD_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT target_id, x1, y1, x2, y2, checklist_confirm FROM target_area "
        "WHERE target_id=?", (target_id,)).fetchone()
    conn.close()
    if not row:
        raise RectNotProven("no rect registered for %r" % target_id)
    if str(row["checklist_confirm"]).strip().lower() != "yes":
        raise RectNotProven(
            "rect %r is not proven (checklist_confirm=%r). Verify it before "
            "clicking: a stored rect was measured 233px from its target."
            % (target_id, row["checklist_confirm"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="fix mismatches + lessons")
    ap.add_argument("--watch", type=int, default=0,
                    help="re-audit every N seconds (0 = once)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.watch:
        print("watching: auditing every %ds (Ctrl+C to stop)" % args.watch)
        while True:
            out = audit_rects(apply_fixes=args.apply)
            print("[%s] judged=%s mismatched=%s not_applicable=%s corrected=%s"
                  % (time.strftime("%H:%M:%S"), out.get("judged"),
                     out.get("mismatched"), out.get("not_applicable"),
                     out.get("corrected")))
            for f in out.get("findings", []):
                print("    %s %s gap=%s via=%s"
                      % (f["kind"], f["target_id"], f.get("gap_px"), f.get("via", "")))
            time.sleep(args.watch)

    out = audit_rects(apply_fixes=args.apply, verbose=not args.json)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
