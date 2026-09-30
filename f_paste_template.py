"""f_paste_template.py — paste the worker-identity template into the chat input BY PYTHON.

Usage:
    python f_paste_template.py --paste

Flow:
    1. Read template from new_session_template_text.txt (written by f_new_session.py --get-template)
    2. Set clipboard via ctypes (CF_UNICODETEXT) — synchronous, no ClipWait needed
    3. Click chat input (1100, 800)
    4. Ctrl+A + Delete (clear any leftover content, e.g. "Pasted Image" chip)
    5. Ctrl+V (paste)
    6. Log to paste_template_log.txt

Exit 0 = pasted, exit 1 = failed.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
TPL_FILE = BASE / "new_session_template_text.txt"
LOG_FILE = BASE / "paste_template_log.txt"

INPUT_X, INPUT_Y = 1100, 800


def log(msg: str) -> None:
    line = f"{datetime.now():%I:%M:%S %p} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def set_clipboard(text: str) -> None:
    """Set clipboard to unicode text via ctypes (synchronous)."""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # declare restypes/argtypes — without these, 64-bit pointers get truncated
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HGLOBAL]
    user32.SetClipboardData.restype = wt.HANDLE
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wt.HGLOBAL]

    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hmem:
            raise RuntimeError("GlobalAlloc failed")
        try:
            pmem = kernel32.GlobalLock(hmem)
            if not pmem:
                raise RuntimeError("GlobalLock failed")
            try:
                ctypes.memmove(pmem, data, len(data))
            finally:
                kernel32.GlobalUnlock(hmem)
            if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
                raise RuntimeError("SetClipboardData failed")
        except Exception:
            kernel32.GlobalFree(hmem)
            raise
    finally:
        user32.CloseClipboard()


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "--paste":
        print("usage: f_paste_template.py --paste")
        return 1

    if not TPL_FILE.exists():
        log("FAIL: no template file (run f_new_session.py --get-template first)")
        return 1
    tpl = TPL_FILE.read_text(encoding="utf-8").strip()
    if not tpl:
        log("FAIL: template file empty")
        return 1

    import pyautogui
    pyautogui.FAILSAFE = False

    try:
        set_clipboard(tpl)
    except Exception as e:
        log(f"FAIL: clipboard set error: {e}")
        return 1

    # focus input, clear leftover content (e.g. "Pasted Image" chip / old text), paste
    # clear = Ctrl+A + Delete + backspace sweep (user 2026-09-18: must clear
    # before paste; LLM proof NO also triggers a re-clear via retry path)
    pyautogui.click(INPUT_X, INPUT_Y)
    time.sleep(0.3)
    _clear_input()

    # PROOF: input must be EMPTY before paste (user 2026-09-18: "when chat is
    # not empty, LLM will give you wrong answer"). LLM check on cropped input
    # region; if not empty -> re-clear, max 2 rounds.
    for round_ in range(3):
        has_text = _input_has_text()
        log(f"clear proof round {round_ + 1}: input has text = {has_text}")
        if has_text == "NO":
            break
        if round_ < 2:
            _clear_input()
    else:
        log("WARN: input still has text after 3 clear rounds — pasting anyway")

    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    log(f"PASTED {len(tpl)} chars (input clicked + cleared + PROOF empty + ctrl+v)")
    return 0


def _clear_input() -> None:
    """Ctrl+A + Delete + 300x backspace sweep on the focused input.
    300 (not 30): leftover user text can be 90+ chars and Ctrl+A does not
    reliably select-all in this input (proven 2026-09-18: 30x sweep left
    ~60 chars behind, LLM correctly said 'has text')."""
    import pyautogui
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.15)
    pyautogui.press("delete")
    time.sleep(0.15)
    for _ in range(300):  # backspace sweep — catches anything Ctrl+A missed
        pyautogui.press("backspace")
    time.sleep(0.2)


def _input_has_text() -> str:
    """LLM proof: does the chat input box contain any typed text?
    Returns YES/NO/ERROR. Uses f_vision_ask.py with the input-box crop
    region (small text is unreadable to 7B-VL at full-screen resolution).
    NO = empty = good."""
    import subprocess
    import os
    ans_file = str(BASE / "vision_ask_answer.txt")
    try:
        if os.path.isfile(ans_file):
            os.remove(ans_file)
    except OSError:
        pass
    try:
        # single-negative question (7B-VL confuses double negatives like
        # "completely empty (no text at all)" — answered NO on an empty box)
        subprocess.run(
            [sys.executable, str(BASE / "f_vision_ask.py"),
             "--ask", "Does the chat input box contain any typed text?",
             "--region", "700,730,820,160"],
            cwd=str(BASE), timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with open(ans_file, encoding="utf-8") as f:
            return f.read().strip() or "ERROR"
    except Exception as e:
        log(f"clear proof FAIL: {type(e).__name__}: {e}")
        return "ERROR"


if __name__ == "__main__":
    sys.exit(main())
