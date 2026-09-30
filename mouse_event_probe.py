# -*- coding: utf-8 -*-
"""mouse_event_probe.py — distinguish a REAL click from optical-mouse drift.

THE LONG-STANDING QUESTION
--------------------------
`test_rect_click_gate.py` asserted `pyautogui.position()` before == after to
prove "no click happened". It was flaky: a few pixels of optical-sensor drift
failed it (observed 735,848 -> 737,851). Comparing coordinates cannot answer
"did a click happen", because the cursor moves for reasons that are not clicks.

THE FINDING (Windows-native, not a workaround)
----------------------------------------------
Microsoft MSLLHOOKSTRUCT documents:

    flags — The event-injected flags. Testing LLMHF_INJECTED (bit 0) will tell
    you whether the event was injected.

        LLMHF_INJECTED           0x00000001  injected from any process
        LLMHF_LOWER_IL_INJECTED  0x00000002  injected from lower integrity

So the discriminator is NOT the coordinate, it is the EVENT FLAG:

    optical-mouse drift   -> flag CLEAR  (a real hardware event, but not a click)
    SendInput click       -> flag SET    (injected by us)

Coordinates drift; event flags do not. `pynput` already exposes this: its win32
backend installs a low-level hook via SetWindowsHookEx and hands the
MSLLHOOKSTRUCT to `win32_event_filter(msg, data)`. This module does the same
with ctypes directly, so no new dependency is added.

HONEST LIMIT
------------
The hook is system-wide (WH_MOUSE_LL) and needs a message loop in the thread
that installed it. If the hook cannot be installed, `injected_clicks` is None
(NOT 0) — an unproven "no click" must never read as a proven one. Same rule as
evidence_classify: an uncertain result is never a silent pass.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Any

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_MOUSE_LL = 14
HC_ACTION = 0

# From MSLLHOOKSTRUCT (winuser.h)
LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002
INJECTED_BITS = LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208

BUTTON_DOWN_MESSAGES = {WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN}
WM_QUIT = 0x0012


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# DECLARE THE PROTOTYPES. Without them ctypes infers C int for every argument,
# and the 64-bit LPARAM coming back out of the hook overflows:
#     ctypes.ArgumentError: argument 4: OverflowError: int too long to convert
# That exception fires on the `return CallNextHookEx(...)` line — i.e. AFTER the
# event was already recorded — so events still appeared and the run looked fine
# while the hook chain was NOT being continued. Relying on "exception ignored"
# here would leave a system-wide hook that never passes events on.
user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class MouseEventRecorder:
    """Collects low-level mouse events for the duration of a `with` block.

    Usage:
        with MouseEventRecorder() as rec:
            do_the_thing()
        print(rec.injected_clicks)   # int if proven, None if NOT observed
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.hook_installed = False
        self.error = ""
        self.chain_errors = 0          # CallNextHookEx failures (must stay 0)
        self._lock = threading.Lock()
        self._hook = None
        self._proc = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    # -- hook callback -----------------------------------------------------
    def _on_hook(self, n_code, w_param, l_param):
        if n_code == HC_ACTION and l_param:
            try:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                msg = int(w_param)
                extra = 0
                try:
                    if info.dwExtraInfo:
                        extra = int(info.dwExtraInfo[0])
                except Exception:
                    extra = 0
                with self._lock:
                    self.events.append({
                        "msg": msg,
                        "x": int(info.pt.x),
                        "y": int(info.pt.y),
                        "flags": int(info.flags),
                        "injected": bool(int(info.flags) & LLMHF_INJECTED),
                        "lower_il": bool(int(info.flags) & LLMHF_LOWER_IL_INJECTED),
                        "extra": extra,
                        "is_button_down": msg in BUTTON_DOWN_MESSAGES,
                        "is_move": msg == WM_MOUSEMOVE,
                    })
            except Exception as e:      # never let the hook raise into the OS
                with self._lock:
                    self.events.append({"error": "%s: %s" % (type(e).__name__, e)})
        # Continue the hook chain. Counted, not swallowed: a hook that stops
        # passing events on still "works" for us and breaks input for every
        # other process.
        try:
            return user32.CallNextHookEx(None, n_code, w_param, l_param)
        except Exception:
            self.chain_errors += 1
            return 0

    # -- lifecycle ---------------------------------------------------------
    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        # Keep a reference to the callback: if it is garbage collected the OS
        # calls freed memory and the process crashes.
        self._proc = HOOKPROC(self._on_hook)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        if not self._hook:
            self.error = "SetWindowsHookExW failed (err=%d)" % ctypes.get_last_error()
            self._ready.set()
            return
        self.hook_installed = True
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def __enter__(self) -> "MouseEventRecorder":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return self

    def __exit__(self, *exc) -> None:
        # A leaked system-wide hook would keep injecting into our callback and
        # can hang the pointer, so unhook in a finally-equivalent path.
        try:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
        finally:
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if self._thread:
                self._thread.join(timeout=2.0)

    # -- queries (None means UNKNOWN, never "no click") --------------------
    def _count(self, predicate) -> int | None:
        if not self.hook_installed:
            return None
        with self._lock:
            return sum(1 for e in self.events if predicate(e))

    @property
    def injected_clicks(self) -> int | None:
        return self._count(lambda e: e.get("injected") and e.get("is_button_down"))

    @property
    def hardware_clicks(self) -> int | None:
        return self._count(lambda e: not e.get("injected") and e.get("is_button_down"))

    @property
    def injected_moves(self) -> int | None:
        return self._count(lambda e: e.get("injected") and e.get("is_move"))

    @property
    def hardware_moves(self) -> int | None:
        return self._count(lambda e: not e.get("injected") and e.get("is_move"))

    def all_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)

    def summary(self) -> dict[str, Any]:
        return {
            "hook_installed": self.hook_installed,
            "error": self.error,
            "chain_errors": self.chain_errors,
            "events": len(self.all_events()),
            "injected_clicks": self.injected_clicks,
            "hardware_clicks": self.hardware_clicks,
            "injected_moves": self.injected_moves,
            "hardware_moves": self.hardware_moves,
        }


class ProbeUnavailable(RuntimeError):
    """Raised when 'no click' could not be PROVEN (hook not installed)."""


def assert_no_injected_click(rec: MouseEventRecorder, *, context: str = "") -> int:
    """Return the injected-click count, or RAISE if that count is unproven.

    A flaky positional check is bad; a check that silently passes when it could
    not observe anything is worse. If the hook failed to install the answer is
    UNKNOWN, and UNKNOWN must not be reported as "no click happened".
    """
    n = rec.injected_clicks
    if n is None:
        raise ProbeUnavailable(
            "cannot prove 'no injected click'%s: hook not installed (%s)"
            % ((" during " + context) if context else "", rec.error or "unknown")
        )
    return n


if __name__ == "__main__":
    import json
    import sys as _sys

    _secs = float(_sys.argv[1]) if len(_sys.argv) > 1 else 3.0
    print("installing WH_MOUSE_LL hook for %ss \u2014 move the mouse to see hardware events"
          % _secs)
    with MouseEventRecorder() as rec:
        time.sleep(_secs)
    print(json.dumps(rec.summary(), indent=2))
