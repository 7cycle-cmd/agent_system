# -*- coding: utf-8 -*-
"""F6 self-debug: capture AHK error dialog text + full-screen screenshot.

Tool #3 in hotkey_tools.md — TOOL.F6.SELF.DEBUG
When an AHK script pops an error/warning dialog, this tool:
  1. finds the AutoHotkey64 process
  2. enumerates its visible windows; any window that is NOT the 160x36
     coord GUI is treated as an error dialog
  3. extracts the RICHEDIT error text via EM_GETTEXTEX
  4. grabs a full-screen screenshot (PIL ImageGrab)
  5. saves both to debug_shots\f6_{timestamp}.png / .txt and logs

Usage:
  python f6_self_debug.py            # capture if dialog present

Exit codes: 0 dialog found + captured, 2 no dialog (screenshot saved),
            1 unexpected error.
"""
import ctypes
import datetime
import os
import subprocess
import sys

import ctypes.wintypes as wt

BASE = r"C:\projects\agent_system"
SHOTS = os.path.join(BASE, "debug_shots")
LOG = os.path.join(BASE, "f9_log.txt")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
# CRITICAL: default SendMessageW restype truncates pointer-sized returns
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t

EM_GETTEXTEX = 0xD4D
COORD_GUI_W, COORD_GUI_H = 160, 36


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s F6-DBG: %s\n" % (
                datetime.datetime.now().strftime("%I:%M %p"), msg))
    except Exception:
        pass


def get_ahk_pids():
    """PIDs of all AutoHotkey64 processes (via PowerShell Get-Process).

    Toolhelp snapshot via ctypes is fragile (restype truncation,
    error-code loss); Get-Process is simpler and reliable.
    """
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process AutoHotkey64 -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty Id) -join ','"],
            timeout=10)
        return {int(x) for x in out.decode().split(",") if x.strip()}
    except Exception:
        return set()


def _has_richedit(h):
    """True if the window has a RICHEDIT child (real error dialog marker).

    Tooltips and the coord GUI have no RICHEDIT, so this filters them out.
    """
    ENUMCHILDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    found = []

    def child(ch, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(ch, cls, 64)
        if cls.value.upper().startswith("RICHEDIT"):
            found.append(1)
        return True

    user32.EnumChildWindows(h, ENUMCHILDPROC(child), 0)
    return bool(found)


def find_dialogs(ahk_pids):
    """Visible AHK windows that contain a RICHEDIT child = error dialogs.

    (Tooltips and the 160x36 coord GUI have no RICHEDIT -> filtered out.)
    """
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    dialogs = []

    def cb(h, _):
        if user32.IsWindowVisible(h):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in ahk_pids and _has_richedit(h):
                r = wt.RECT()
                user32.GetWindowRect(h, ctypes.byref(r))
                dialogs.append((h, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return dialogs


def read_richedit(dlg_hwnd):
    """Extract text from the RICHEDIT child of the dialog via WM_GETTEXT.

    WM_GETTEXT works on RICHEDIT and is far simpler than EM_GETTEXTEX
    (which needs a TEXTEXINFO struct and trips ctypes type checks).
    """
    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E
    ENUMCHILDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    texts = []

    def child(h, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if not cls.value.upper().startswith("RICHEDIT"):
            return True
        n = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.SendMessageW(h, WM_GETTEXT, n + 1, ctypes.addressof(buf))
            if buf.value:
                texts.append(buf.value)
        return True

    user32.EnumChildWindows(dlg_hwnd, ENUMCHILDPROC(child), 0)
    return "\n".join(texts)


def screenshot(path):
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.save(path)
    return img.size


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(SHOTS, exist_ok=True)
    shot_path = os.path.join(SHOTS, "f6_%s.png" % ts)
    txt_path = os.path.join(SHOTS, "f6_%s.txt" % ts)

    ahk_pids = get_ahk_pids()
    if not ahk_pids:
        log("no AutoHotkey64 process found")
        print("NO AHK PROCESS")
        sys.exit(1)

    dialogs = find_dialogs(ahk_pids)
    size = screenshot(shot_path)
    log("screenshot saved: %s (%dx%d)" % (shot_path, size[0], size[1]))

    if not dialogs:
        log("no error dialog found (AHK pids=%s)" % sorted(ahk_pids))
        print("NO DIALOG: screenshot saved to %s" % shot_path)
        sys.exit(2)

    all_text = []
    for h, w, hgt in dialogs:
        t = read_richedit(h)
        all_text.append("=== dialog %dx%d ===\n%s" % (w, hgt, t or "(empty)"))
    report = "\n\n".join(all_text)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report)
    log("captured %d dialog(s), text -> %s" % (len(dialogs), txt_path))
    print("DIALOG CAPTURED: %d dialog(s)" % len(dialogs))
    print("TEXT FILE: %s" % txt_path)
    print("SHOT FILE: %s" % shot_path)
    print("---- error text ----")
    print(report[:2000])
    sys.exit(0)


if __name__ == "__main__":
    main()
