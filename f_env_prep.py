# -*- coding: utf-8 -*-
"""
f_env_prep.py — LLM environment preparation (user spec 2026-09-18).

Flow:
  1) Click chat input box
  2) Type "123" (marker)
  3) Screenshot (crop input region, 2x upscale)
  4) LLM: "Does the image contain ONLY the number 123? YES or NO"
     - YES (4A) → environment ready → Ctrl+A + Ctrl+V (paste template)
     - NO  (4B/4C) → fix environment:
         - Check if 123 is present (2nd LLM question)
         - 4B (123 + other): Ctrl+A + type 123
         - 4C (no 123): click input + type 123
         - Redo 3) + 4)
     - Max 3 rounds, then proceed anyway (log WARN)

Usage:
    python f_env_prep.py --prep

Exit 0 = environment ready (template pasted), 1 = error.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_FILE = BASE / "env_prep_log.txt"
TPL_FILE = BASE / "new_session_template_text.txt"
ANSWER_FILE = BASE / "vision_ask_answer.txt"

FALLBACK_INPUT = (1100, 800)
REGION = "700,730,820,160"  # input box crop (updated after measurement)
MAX_ROUNDS = 3


def set_region(x: int, y: int) -> None:
    """Re-center the LLM crop region on the measured input box.
    Also persisted to env_prep_region.txt so the AHK paste-proof uses
    the SAME crop (not a guessed fixed region)."""
    global REGION
    REGION = f"{x - 410},{y - 80},820,160"
    try:
        (BASE / "env_prep_region.txt").write_text(REGION, encoding="utf-8")
    except OSError:
        pass


def find_input_box() -> tuple[int, int]:
    """MEASURE the chat input box (user 2026-09-18: "click at the middle,
    mouse focus will at the chatbox"). The box is a rounded rectangle with a
    visible bright border. Deterministic detection: scan a VERTICAL column at
    the box center x for bright horizontal border lines (value > 90). The box
    is the bottom-most bordered element -> take the last bright line as the
    bottom border and the bright line just above it (gap > 40px) as the top
    border. Click their middle.
    Returns (x, y) center; falls back to (1100, 871) if not found."""
    import pyautogui
    import numpy as np
    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()
    g = np.array(shot.convert("L"), dtype=np.int16)
    cx = 1100  # box center x (chat panel, maximized 1920x1080)
    # vertical scan of the column at cx, over the bottom area
    y0, y1 = 750, 990
    col = g[y0:y1, cx]
    bright = [y0 + i for i, v in enumerate(col) if v > 90]
    if len(bright) < 2:
        log(f"find_input_box: <2 bright border lines (found {len(bright)}) — fallback")
        return (1100, 871)
    # bottom border = last bright line; top border = the bright line above it
    # with a plausible box gap (>40px)
    bottom = bright[-1]
    top = None
    for r in reversed(bright[:-1]):
        if bottom - r > 40:
            top = r
            break
    if top is None:
        top = bright[0]
    if bottom - top < 40:
        log(f"find_input_box: box too small ({bottom - top}px) — fallback")
        return (1100, 871)
    cy = (top + bottom) // 2
    log(f"find_input_box: box top={top} bottom={bottom} -> center ({cx},{cy})")
    return cx, cy


def log(msg: str) -> None:
    from datetime import datetime
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def _vision_ask(question: str, region: str = "") -> str:
    """Ask 7B-VL a yes/no question. Returns YES/NO/ERROR."""
    try:
        if ANSWER_FILE.exists():
            ANSWER_FILE.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(BASE / "f_vision_ask.py"), "--ask", question]
    if region:
        cmd += ["--region", region]
    try:
        subprocess.run(cmd, cwd=str(BASE), timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ANSWER_FILE.exists():
            return ANSWER_FILE.read_text(encoding="utf-8").strip() or "ERROR"
    except Exception as e:
        log(f"vision_ask FAIL: {type(e).__name__}: {e}")
    return "ERROR"


def _type_123() -> None:
    """Type '123' into the focused input."""
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.typewrite("123", interval=0.05)
    time.sleep(0.3)


def _clear_and_type_123() -> None:
    """Ctrl+A + type 123 (replaces all content)."""
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.15)
    pyautogui.press("delete")
    time.sleep(0.1)
    _type_123()


def _click_and_type_123() -> None:
    """Click input (measured middle) + type 123."""
    import pyautogui
    pyautogui.FAILSAFE = False
    x, y = find_input_box()
    pyautogui.click(x, y)
    time.sleep(0.3)
    _type_123()


def _set_clipboard(text: str) -> None:
    """Set clipboard via ctypes (64-bit safe)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.OpenClipboard.restype = wintypes.BOOL
    u.EmptyClipboard.argtypes = []
    u.EmptyClipboard.restype = None
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    u.CloseClipboard.argtypes = []
    u.CloseClipboard.restype = wintypes.BOOL
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.restype = wintypes.BOOL
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, ctypes.c_size_t(len(data)))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        p = k.GlobalLock(h)
        if not p:
            raise RuntimeError("GlobalLock failed")
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u.CloseClipboard()


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "--prep":
        print("usage: f_env_prep.py --prep")
        return 1

    import pyautogui
    pyautogui.FAILSAFE = False

    # 1) MEASURE input box + click its middle (focus lands in chat box)
    ix, iy = find_input_box()
    set_region(ix, iy)
    pyautogui.click(ix, iy)
    time.sleep(0.3)
    log(f"env_prep: clicked input middle ({ix},{iy}), region={REGION}")

    # 2) Type 123
    _type_123()
    log("env_prep: typed 123")

    # 3-4) LLM proof loop
    for round_ in range(1, MAX_ROUNDS + 1):
        time.sleep(0.5)
        # LLM: "only 123?"
        only_123 = _vision_ask(
            "Does the image contain ONLY the number 123 and nothing else?",
            REGION
        )
        log(f"env_prep round {round_}: only_123 = {only_123}")

        if only_123 == "YES":
            # 4A: environment ready → Ctrl+A + Ctrl+V (paste template)
            # LLM CONFIRMED focus: "123" is inside the input box, so keyboard
            # focus IS in the input box → paste target confirmed (user 2026-09-18:
            # "LLM preparation help to confirm mouse focus for pasting too")
            log("env_prep: 4A — FOCUS CONFIRMED (123 is in input box), paste target = input box")
            if not TPL_FILE.exists():
                log("env_prep FAIL: no template file")
                return 1
            tpl = TPL_FILE.read_text(encoding="utf-8").strip()
            if not tpl:
                log("env_prep FAIL: template empty")
                return 1
            _set_clipboard(tpl)
            # re-click measured middle right before paste → focus guaranteed
            # in the LLM-confirmed box (no focus drift between proof and paste)
            fx, fy = find_input_box()
            pyautogui.click(fx, fy)
            time.sleep(0.3)
            log(f"env_prep: re-clicked input middle ({fx},{fy}) before paste")
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)
            log(f"env_prep: PASTED {len(tpl)} chars (Ctrl+A + Ctrl+V)")
            return 0

        # NO → 4B or 4C: check if 123 is present
        has_123 = _vision_ask(
            "Does the image contain the number 123?",
            REGION
        )
        log(f"env_prep round {round_}: has_123 = {has_123}")

        if has_123 == "YES":
            # 4B: 123 + other text → Ctrl+A + retype 123
            log("env_prep: 4B — 123 + other, Ctrl+A + retype 123")
            _clear_and_type_123()
        else:
            # 4C: no 123 → click + type 123
            log("env_prep: 4C — no 123, click + type 123")
            _click_and_type_123()

    log("env_prep WARN: max rounds reached, pasting anyway")
    # Fallback: paste template regardless
    if TPL_FILE.exists():
        tpl = TPL_FILE.read_text(encoding="utf-8").strip()
        if tpl:
            _set_clipboard(tpl)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)
            log(f"env_prep: PASTED {len(tpl)} chars (fallback)")
            return 0
    log("env_prep FAIL: no template")
    return 1


if __name__ == "__main__":
    sys.exit(main())
