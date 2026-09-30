# -*- coding: utf-8 -*-
"""Shared CDP + clipboard infrastructure for hotkey tools.

Tools that need Chrome DevTools Protocol import from here instead of
duplicating code, so tools can be composed (e.g. F9 = env-check + copy).

Provides:
  cdp_ready(timeout)      -> bool: is port 9222 answering?
  launch_chrome(url)      -> bool: launch CDP Chrome instance, wait for port
  ensure_cdp(tag)         -> (ok, action): "already" | "launched" | "failed"
  find_tab(substr)        -> tab dict or None
  cdp_evaluate(tab, expr) -> value: Runtime.evaluate with returnByValue
  set_clipboard(text)     -> None: persistent ctypes clipboard write
  log(msg, tag)           -> None: timestamped append to f9_log.txt

Exit-code convention for tool entry scripts:
  0 ok, 1 CDP unreachable (even after auto-launch), 2 no tab, 3 no content.
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.request

CDP = "http://127.0.0.1:9222"
CDP_PORT = 9222
LOG = r"C:\projects\agent_system\f9_log.txt"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PROFILE = r"C:\projects\agent_system\chrome_cdp_profile"
DEFAULT_URL = "https://chat.deepseek.com/a/chat/s/7e589851-81bf-4e63-a3b6-205a3ee6a7fd"


def log(msg, tag="CDP"):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s: %s\n" % (
                datetime.datetime.now().strftime("%I:%M %p"), tag, msg))
    except Exception:
        pass


def cdp_ready(timeout=3):
    """True if the CDP HTTP endpoint answers."""
    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=timeout) as r:
            json.load(r)
            return True
    except Exception:
        return False


def _wait_ready(timeout=25):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp_ready():
            return True
        import time as _t
        _t.sleep(0.5)
    return cdp_ready()


def launch_chrome(url=DEFAULT_URL, wait_timeout=25):
    """Launch the dedicated CDP Chrome instance (separate profile).

    Chrome 136+ blocks --remote-debugging-port on the default user-data-dir,
    so a dedicated profile dir is required. Chrome 153 requires
    --remote-allow-origins=* or the WebSocket handshake returns 403.
    Returns True if the port is answering after launch.
    """
    if not os.path.exists(CHROME):
        log("FAIL: chrome.exe not found at %s" % CHROME)
        return False
    cmd = [
        CHROME,
        "--remote-debugging-port=%d" % CDP_PORT,
        "--remote-allow-origins=*",
        "--user-data-dir=%s" % CDP_PROFILE,
        url,
    ]
    try:
        subprocess.Popen(cmd, cwd=os.path.dirname(CHROME))
    except Exception as e:
        log("FAIL: chrome launch exception: %s" % e)
        return False
    log("launched chrome, waiting for port %d..." % CDP_PORT)
    return _wait_ready(wait_timeout)


def ensure_cdp(tag="CDP", url=DEFAULT_URL):
    """Make sure CDP is up. Returns (ok, action).

    action: "already" (port was up), "launched" (we started it), "failed".
    """
    if cdp_ready():
        log("CDP already running (port %d)" % CDP_PORT, tag)
        return True, "already"
    log("CDP down, auto-launching Chrome...", tag)
    if launch_chrome(url):
        log("CDP up after auto-launch", tag)
        return True, "launched"
    log("FAIL: CDP still down after auto-launch attempt", tag)
    return False, "failed"


def get(path):
    with urllib.request.urlopen(CDP + path, timeout=5) as r:
        return json.load(r)


def find_tab(substr="deepseek"):
    """Find the first page tab whose URL contains substr (case-insensitive)."""
    for t in get("/json"):
        if t.get("type") == "page" and substr in t.get("url", "").lower():
            return t
    return None


def cdp_evaluate(tab, expr):
    """Connect to tab's WebSocket, run Runtime.evaluate, return value."""
    import websocket
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
    try:
        mid = [0]

        def send(method, params=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": method,
                                "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == mid[0]:
                    return msg

        r = send("Runtime.evaluate",
                 {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def cdp_send(tab, method, params=None):
    """Generic CDP command on a tab's WebSocket (e.g. Input.insertText).

    cdp_evaluate() only wraps Runtime.evaluate; tools that need a real input
    event (Input.insertText / Input.dispatchKeyEvent) use this instead.
    Returns the raw CDP reply dict.
    """
    import websocket
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
    try:
        ws.send(json.dumps({"id": 1, "method": method,
                            "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg
    finally:
        ws.close()


def read_clipboard_text():
    """Read the current clipboard text via ctypes (works under pythonw).

    64-bit: MUST declare argtypes/restype or pointers get truncated
    -> access violation (same bug class as set_clipboard).
    """
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.OpenClipboard.restype = wintypes.BOOL
    u.GetClipboardData.argtypes = [wintypes.UINT]
    u.GetClipboardData.restype = wintypes.HANDLE
    u.CloseClipboard.argtypes = []
    u.CloseClipboard.restype = wintypes.BOOL
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.restype = wintypes.BOOL
    CF_UNICODETEXT = 13
    if not u.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set_clipboard(text):
    """Robust Windows clipboard via ctypes SetClipboardData (persistent,
    no live owner needed — unlike tkinter which loses content on destroy)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.CloseClipboard.argtypes = []
    user32.EmptyClipboard.argtypes = []
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    if not user32.OpenClipboard(0):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"  # null-terminated
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        try:
            p = kernel32.GlobalLock(h)
            try:
                ctypes.memmove(p, data, len(data))
            finally:
                kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise RuntimeError("SetClipboardData failed")
        except Exception:
            kernel32.GlobalFree(h)
            raise
    finally:
        user32.CloseClipboard()


if __name__ == "__main__":
    # quick self-test: python cdp_common.py
    ok, action = ensure_cdp("SELFTEST")
    print("CDP ok=%s action=%s" % (ok, action))
    if ok:
        tab = find_tab()
        print("deepseek tab:", tab["url"] if tab else None)
    sys.exit(0 if ok else 1)
