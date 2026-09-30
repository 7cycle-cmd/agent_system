"""Tests for the pre-action rect gate (measured, refuse-don't-record).

WHY
---
Two industry references were checked (2026-09-20) and both ENFORCE at the point
of action:

  * Playwright re-checks before every click (one element, Visible, Stable,
    Receives Events, Enabled) and raises TimeoutError if a check does not pass.
  * Selenium raises StaleElementReferenceException and documents the remedy as
    "always relocate the element every time you go to use it"; it raises
    ElementClickInterceptedException when the target is obscured.

Our gap was not the check — `checklist_confirm='no'` was CORRECTLY recorded. The
gap was that the click proceeded anyway. A rect was measured 233px from its
target and still got clicked. These tests pin the enforcement down.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")

import f_perm_click as p  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
COORD_DB = Path(r"C:\projects\agent_system\coords.db")


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print("  %s  %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("        %s" % detail)


def main() -> int:
    print("=== test_rect_click_gate ===")

    src = Path(p.__file__).read_text(encoding="utf-8")

    # ---- 1. the guard exists in the source -------------------------------
    check("_open_picker_via_pill refuses on checklist_confirm != 'yes'",
          "checklist_confirm=%r (need 'yes')" in src)
    check("the refusal logs a REFUSED line",
          "picker REFUSED" in src)
    check("the refusal happens BEFORE mouse_click",
          src.index("picker REFUSED") < src.index("mouse_click(cx, cy)"),
          "the click call appears before the guard")

    # ---- 2. live: an unproven rect is refused ---------------------------
    conn = sqlite3.connect(str(COORD_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT target_id, y1, y2, checklist_confirm FROM target_area "
        "WHERE target_id='perm_pill'").fetchone()
    conn.close()
    check("perm_pill row exists", row is not None)
    if row:
        print("  perm_pill y %s..%s confirm=%r"
              % (row["y1"], row["y2"], row["checklist_confirm"]))
        check("perm_pill is currently unproven (confirm != yes) as expected",
              str(row["checklist_confirm"]).lower() != "yes")

    # ---- 3. live: the call returns False rather than clicking -----------
    import cdp_common
    import mouse_event_probe as mep

    log_path = Path(cdp_common.LOG)
    try:
        before_len = log_path.stat().st_size if log_path.is_file() else 0
    except Exception:
        before_len = 0

    # WHY THIS REPLACED THE CURSOR CHECK -------------------------------
    # "cursor did not move" compared REAL cursor coords for exact equality, so
    # optical-mouse drift failed it (observed 735,848 -> 737,851 — 3px). A
    # coordinate cannot answer "did a click happen"; the cursor moves for
    # reasons that are not clicks.
    # Windows answers it directly: MSLLHOOKSTRUCT.flags carries
    # LLMHF_INJECTED (bit 0), SET for injected input and CLEAR for hardware
    # events. Coordinates drift; event flags do not. So the property asserted
    # here is stronger AND path-independent:
    #     "this call produced ZERO injected clicks"
    # and if the hook cannot observe, assert_no_injected_click RAISES rather
    # than reporting a silent pass.
    try:
        with mep.MouseEventRecorder() as rec:
            result = p._open_picker_via_pill()
            injected = mep.assert_no_injected_click(rec, context="refused pill click")
        hook_ok = True
    except mep.ProbeUnavailable as e:
        hook_ok = False
        injected = None
        result = False
        check("injected-event probe AVAILABLE (hook installed)", False, str(e))
    except Exception as e:
        hook_ok = False
        injected = None
        result = False
        check("injected-event probe AVAILABLE (hook installed)", False,
              "%s: %s" % (type(e).__name__, e))

    if hook_ok:
        check("injected-event probe AVAILABLE (hook installed)", True)
        check("returns False for an unproven rect", result is False,
              "returned %r" % result)
        check("the call produced ZERO injected clicks (no click happened)",
              injected == 0, "injected_clicks=%r" % (injected,))

    # The refusal must also be LOGGED — independent evidence of which branch ran.
    # Only the bytes THIS call appended are read: the log is append-only, so
    # reading the whole file could pass on a stale line from an earlier run.
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(before_len)
            new_log = f.read()
    except Exception:
        new_log = ""
    check("the refusal was LOGGED by THIS call (new bytes only)",
          "picker REFUSED" in new_log,
          "new log bytes=%d" % len(new_log))

    # ---- 4. the corrected rect is where the pill actually is ------------
    if row:
        check("perm_pill was moved into the footer (y > 900)",
              int(row["y1"]) > 900,
              "y1=%s — still in the upper screen?" % row["y1"])
        check("perm_pill height is a plausible row height (20..60px)",
              20 <= int(row["y2"]) - int(row["y1"]) <= 60,
              "height=%d" % (int(row["y2"]) - int(row["y1"])))

    # ---- 5. MUTATION: prove the guard is load-bearing -------------------
    # If 'yes' bypasses the guard, then the guard is what blocks 'no'. We do NOT
    # flip the real row (that would click for real); we assert the branch exists
    # by string, and that 'no' is the value in play.
    check("MUTATION: the guard is conditional on the confirm value",
          'if confirm != "yes":' in src,
          "expected an explicit conditional, not an unconditional block")
    check("MUTATION: so setting confirm='yes' is what would allow the click",
          'confirm = str(row.get("checklist_confirm") or "").strip().lower()' in src)

    print()
    n = sum(1 for _, ok, _ in CHECKS if ok)
    print("%d/%d checks passed" % (n, len(CHECKS)))
    return 0 if n == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
