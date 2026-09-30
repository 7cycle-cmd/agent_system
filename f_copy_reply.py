# -*- coding: utf-8 -*-
"""
Tool #9 helper: f_copy_reply.py — copy the latest assistant reply via the
chat COPY button (hover-revealed icon), then extract the SESSION_ID from
the clipboard text.

WHY copy-button (not vision OCR): the reply text is long; clicking the
native copy icon puts the EXACT text on the clipboard — deterministic,
no OCR errors. The copy icon only appears on hover, so this script moves
the mouse over the reply area first.

Usage:
    python f_copy_reply.py --copy-reply   # hover -> find copy icon -> click
                                         # -> read clipboard -> extract
                                         # SESSION_ID -> "SESSION_ID: <uuid>"
                                         # -> record chat_reply_log row
                                         #    (sent_at + elapsed_ms)
    python f_copy_reply.py --check        # deps + template file ok?

Output: "SESSION_ID: <uuid>" on success, or "FAIL: reason" (exit 1).
Writes session_id_last.txt (the bare uuid) for AHK to read.
Reads new_session_sent_at.txt (epoch ms, written by AHK at send time)
to compute elapsed_ms for the chat_reply_log row.
Run with pythonw.exe from AHK (no console window flash).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SESSION_ID_FILE = os.path.join(ROOT, "session_id_last.txt")
SENT_AT_FILE = os.path.join(ROOT, "new_session_sent_at.txt")
VISION_LOG = os.path.join(ROOT, "copy_reply_log.txt")
COPY_TEMPLATE = os.path.join(ROOT, "copy_btn_template.png")

# SESSION_ID line: "SESSION_ID: <uuid>" (uuid = 8-4-4-4-12 hex)
SESSION_ID_RE = re.compile(
    r"SESSION_ID:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def vlog(msg: str) -> None:
    """Append to copy_reply_log.txt (pythonw swallows stdout; AHK needs a file)."""
    import datetime
    try:
        with open(VISION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def local_snapshot() -> bytes:
    """Capture full screen LOCALLY via pyautogui -> PNG bytes (no window popup)."""
    import io
    import pyautogui
    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()  # already a PIL Image
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    return buf.getvalue()


def find_copy_icon(png: bytes) -> tuple[int, int] | None:
    """Template-match the hover-revealed copy icon in the chat reply area.
    Returns the icon center (px, py) in screen coords, or None."""
    import cv2
    import numpy as np
    import io
    from PIL import Image
    if not os.path.isfile(COPY_TEMPLATE):
        vlog("find_copy_icon: template file missing")
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(COPY_TEMPLATE, cv2.IMREAD_COLOR)
    if tpl is None:
        vlog("find_copy_icon: could not read template")
        return None
    H, W = img.shape[:2]
    # search the chat panel area (right of the editor, below the header)
    x0, y0 = int(0.42 * W), int(0.08 * H)
    x1, y1 = int(0.78 * W), int(0.90 * H)
    roi = img[y0:y1, x0:x1]
    if tpl.shape[0] > roi.shape[0] or tpl.shape[1] > roi.shape[1]:
        vlog("find_copy_icon: template larger than ROI")
        return None
    res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
    minv, maxv, minl, maxl = cv2.minMaxLoc(res)
    if maxv < 0.75:
        vlog(f"find_copy_icon: best score {maxv:.3f} < 0.75 -> not found")
        return None
    th, tw = tpl.shape[:2]
    cx = x0 + maxl[0] + tw // 2
    cy = y0 + maxl[1] + th // 2
    vlog(f"find_copy_icon: score={maxv:.3f} center=({cx},{cy})")
    return cx, cy


def _vision_ask(png: bytes, prompt: str) -> str:
    """Ask local Ollama vision a question about the screenshot. Returns raw text."""
    import base64
    import json
    import urllib.request
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode()}"}},
            ],
        }],
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"]


def _crop_upscale(png: bytes, x: int, y: int, w: int, h: int, scale: int = 2) -> bytes:
    """Crop a screen region and upscale it (small UI text is unreadable to the
    7B-VL at full-screen resolution). Same technique as f_vision_ask.py."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    x = max(0, min(x, im.width - 1))
    y = max(0, min(y, im.height - 1))
    w = max(1, min(w, im.width - x))
    h = max(1, min(h, im.height - y))
    crop = im.crop((x, y, x + w, y + h))
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def scroll_chat_to_end() -> None:
    """Scroll the main chat panel to the END so the LATEST reply (and its
    footer / copy button) is in view. Reusable by the finish-gate poller and
    by copy_reply (user 2026-09-18: 'keep scroll to the end until find SHA256')."""
    import pyautogui
    pyautogui.FAILSAFE = False
    sw, sh = pyautogui.size()
    pyautogui.moveTo(int(0.52 * sw), int(0.45 * sh))
    for _ in range(8):
        pyautogui.scroll(-3)
        time.sleep(0.05)
    time.sleep(0.5)


def vision_reply_done(png: bytes) -> bool:
    """Ask the LLM: does the chat reply contain the 'CHAT_SHA256' marker?
    YES = chat finished (reply has the worker-identity footer) -> safe to copy.
    NO  = still generating / wrong content -> keep polling.

    The footer text is small, so we crop the chat panel + upscale 2x before
    asking (full-screen 7B-VL flakily answers NO on tiny text)."""
    prompt = (
        "In this cropped chat panel, does the assistant reply contain the "
        "text 'CHAT_SHA256' (a table row like '| 5 | CHAT_SHA256 | <value> |')? "
        "Answer with ONLY one word: YES or NO."
    )
    try:
        # chat panel region (maximized 1920x1080): x 700-1520, y 100-800
        region = _crop_upscale(png, 700, 100, 820, 700, scale=2)
        raw = _vision_ask(region, prompt)
        vlog(f"vision_reply_done raw: {raw[:200]!r}")
        return "YES" in raw.upper() and "NO" not in raw.upper().replace("YES", "")
    except Exception as e:
        vlog(f"vision_reply_done FAIL: {type(e).__name__}: {e}")
        return False


def _chat_roi() -> tuple[int, int, int, int]:
    """Chat-panel ROI (x0, y0, x1, y1) for the hover-diff. Excludes the input
    box (bottom), the Sessions list (right), and the editor (left) so the diff
    only contains the reply area where the hover icon group appears. Without
    this, a full-screen diff is polluted by the blinking input cursor, the
    updating session list, and any still-generating content -> bbox = whole
    screen (observed 2026-09-18)."""
    import pyautogui
    sw, sh = pyautogui.size()
    x0 = int(0.40 * sw)   # right of the editor
    y0 = int(0.08 * sh)   # below the top toolbar
    x1 = int(0.72 * sw)   # left of the Sessions list
    y1 = int(0.70 * sh)   # above the input box
    return x0, y0, x1, y1


def _mask_outside_roi(im, roi: tuple[int, int, int, int]) -> None:
    """Zero out everything outside the ROI in-place (PIL Image, L or RGB)."""
    from PIL import ImageDraw
    x0, y0, x1, y1 = roi
    W, H = im.size
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, y0], fill=0)        # top
    d.rectangle([0, y1, W, H], fill=0)        # bottom
    d.rectangle([0, y0, x0, y1], fill=0)      # left
    d.rectangle([x1, y0, W, y1], fill=0)      # right


def diff_find_copy_icon(hx: int, hy: int) -> tuple[int, int] | None:
    """DETERMINISTIC icon locate: snapshot without hover, snapshot with hover,
    the changed pixels = the action icons that appeared. Returns the center
    of the diff bounding box (the icon cluster), or None if no clean diff.
    The diff is constrained to the chat-panel ROI so screen-wide changes
    (blinking cursor, session list, generating content) don't pollute it."""
    import pyautogui
    import time
    from PIL import Image, ImageChops, ImageDraw
    import io
    pyautogui.FAILSAFE = False

    def _mask_cursor(im: Image.Image, x: int, y: int) -> Image.Image:
        d = ImageDraw.Draw(im)
        d.rectangle([x - 25, y - 25, x + 25, y + 25], fill=0)
        return im

    roi = _chat_roi()
    # 1) neutral position (top-left corner, away from chat content)
    pyautogui.moveTo(10, 10)
    time.sleep(0.8)
    a = _mask_cursor(Image.open(io.BytesIO(local_snapshot())), 10, 10)
    _mask_outside_roi(a, roi)
    # 2) hover the reply
    pyautogui.moveTo(hx, hy)
    time.sleep(1.2)
    b = _mask_cursor(Image.open(io.BytesIO(local_snapshot())), hx, hy)
    _mask_outside_roi(b, roi)
    if a.size != b.size:
        return None
    diff = ImageChops.difference(a, b).convert("L")
    bbox = diff.getbbox()
    if bbox is None:
        vlog("diff_find: no diff between hover states")
        return None
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    vlog(f"diff_find: bbox={bbox} ({w}x{h})")
    # icon cluster is small; a huge diff = something else changed (ignore)
    if w > 400 or h > 200:
        vlog("diff_find: diff too large — not the icon, skip")
        return None
    # the COPY icon is the RIGHTMOST icon in the cluster (play icon is left)
    cx = bbox[2] - 12
    cy = (bbox[1] + bbox[3]) // 2
    vlog(f"diff_find: icon at ({cx},{cy})")
    return cx, cy


def find_icon_group_bbox(hx: int, hy: int) -> tuple[int, int, int, int] | None:
    """Hover-diff (neutral vs hovered) -> bounding box of the action row
    (4 icons + time + model, same row) that appears on hover, or None.
    Both snapshots are masked to the chat-panel ROI so screen-wide changes
    (blinking input cursor, session list, generating content) are excluded.
    KEY FACT (user 2026-09-19): this row ONLY exists when the reply is
    COMPLETE — while generating there is no action row. So 'group found'
    IS the chat-finished proof; no separate finish signal needed."""
    import pyautogui
    import time
    from PIL import Image, ImageChops, ImageDraw
    import io
    pyautogui.FAILSAFE = False

    def _mask_cursor(im: Image.Image, x: int, y: int) -> Image.Image:
        d = ImageDraw.Draw(im)
        d.rectangle([x - 25, y - 25, x + 25, y + 25], fill=0)
        return im

    roi = _chat_roi()
    pyautogui.moveTo(10, 10)
    time.sleep(0.8)
    a = _mask_cursor(Image.open(io.BytesIO(local_snapshot())), 10, 10)
    _mask_outside_roi(a, roi)
    pyautogui.moveTo(hx, hy)
    time.sleep(1.2)
    b = _mask_cursor(Image.open(io.BytesIO(local_snapshot())), hx, hy)
    _mask_outside_roi(b, roi)
    if a.size != b.size:
        return None
    diff = ImageChops.difference(a, b).convert("L")
    bbox = diff.getbbox()
    if bbox is None:
        vlog("group_find: no diff between hover states")
        return None
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    vlog(f"group_find: group bbox={bbox} ({w}x{h})")
    # a row of icons + time + model: wide-ish, short. Too big = something
    # else changed. (w up to 700: the row includes time + model text.)
    if w < 60 or w > 700 or h > 80:
        vlog("group_find: bbox not a plausible icon group — skip")
        return None
    return bbox


def find_copy_via_group() -> tuple[int, int] | None:
    """Legacy wrapper: locate the icon group at the default hover point and
    return the rightmost-icon position (copy = rightmost, inset 12)."""
    import pyautogui
    sw, sh = pyautogui.size()
    bbox = find_icon_group_bbox(int(0.52 * sw), int(0.45 * sh))
    if bbox is None:
        return None
    group_y = (bbox[1] + bbox[3]) // 2
    cx = bbox[2] - 12
    vlog(f"group_find: copy = (rightmost X {cx}, group Y {group_y})")
    return cx, group_y


def wait_for_icon_group(max_wait: int = 180) -> tuple[int, int, int, int] | None:
    """Poll until the action row (4 icons + time + model) appears on hover.
    The row only exists when the reply is COMPLETE, so this loop IS the
    'chat finished' wait (user 2026-09-19: 'keep scroll to the end until
    find' — the end marker is the icon row, not the SHA256 text).
    Each attempt: scroll chat to end, then try several hover Y positions
    (the row sits at the END of the reply, whose Y depends on reply height).
    Returns the group bbox, or None on timeout. NO LLM in the loop."""
    import pyautogui
    sw, sh = pyautogui.size()
    hover_ys = [int(0.30 * sh), int(0.45 * sh), int(0.60 * sh)]
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        scroll_chat_to_end()
        for hy in hover_ys:
            bbox = find_icon_group_bbox(int(0.52 * sw), hy)
            if bbox is not None:
                vlog(f"icon_row: FOUND on attempt {attempt} (hover y={hy}) "
                     f"bbox={bbox} — chat finished, safe to copy")
                return bbox
        vlog(f"icon_row: attempt {attempt} — no icon row yet (still "
             f"generating?) — wait 6s")
        time.sleep(6)
    vlog(f"icon_row: timeout after {attempt} attempts — no icon row")
    return None


def vision_find_copy_icon(png: bytes) -> tuple[int, int] | None:
    """Ask the LLM to LOCATE the copy icon (two overlapping squares) that
    appears on hover over a chat reply / code block. Returns (x, y) screen
    coords, or None. More robust than template match (icon changes on hover)."""
    prompt = (
        "In this screenshot, a chat reply is hovered and small action icons "
        "are visible on the right side of a code block or reply (a play/triangle "
        "icon and a COPY icon that looks like two overlapping squares). "
        "Find the COPY icon (two overlapping squares). "
        "Answer with ONLY two numbers: x y (pixel coordinates of the icon center), "
        "nothing else."
    )
    try:
        raw = _vision_ask(png, prompt)
        vlog(f"vision_find_copy_icon raw: {raw[:200]!r}")
        nums = re.findall(r"\d{2,4}", raw)
        if len(nums) >= 2:
            x, y = int(nums[-2]), int(nums[-1])
            if 0 < x < 1920 and 0 < y < 1080:
                return x, y
        return None
    except Exception as e:
        vlog(f"vision_find_copy_icon FAIL: {type(e).__name__}: {e}")
        return None


def vision_read_session_id(png: bytes) -> str | None:
    """Fallback: ask the LLM to READ the SESSION_ID value from the reply."""
    prompt = (
        "In the chat panel on the right, the assistant reply has a line "
        "'SESSION_ID: <uuid>'. Read that UUID exactly. "
        "Answer with ONLY the UUID, nothing else."
    )
    try:
        raw = _vision_ask(png, prompt)
        vlog(f"vision_read_session_id raw: {raw[:200]!r}")
        m = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw
        )
        return m.group(0) if m else None
    except Exception as e:
        vlog(f"vision_read_session_id FAIL: {type(e).__name__}: {e}")
        return None


def record_click_success(target_id: str, x: int, y: int) -> None:
    """Record a VERIFIED successful click into the success-target table
    (target_points). Same target_id with different X/Y over runs = the
    learned range, viewable at /settings. Never raises — a DB hiccup must
    not break the SESSION_ID handoff."""
    try:
        import coord_store
        coord_store.record_success(target_id, int(x), int(y), action="click")
        vlog(f"record_click_success: {target_id} at ({x},{y})")
    except Exception as e:
        vlog(f"record_click_success: skipped ({type(e).__name__}: {e})")


def read_clipboard() -> str:
    """Read the current clipboard text via ctypes (works under pythonw).
    64-bit: MUST declare argtypes/restype or pointers get truncated
    -> access violation (same bug class as set_clipboard, fixed 2026-09-18)."""
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


def _activate_code_and_prove(timeout: float = 3.0) -> bool:
    """Activate VS Code by PROCESS and PROVE it is foreground. Returns bool.

    WHY BY PROCESS, NOT TITLE
    -------------------------
    The old code used `FindWindowW(None, "Visual Studio Code")`. VS Code's title
    carries the open file name ("app.js - agent_system - Visual Studio Code"),
    so the exact-title match failed and returned 0. The caller then clicked
    anyway — a silent no-op followed by an action on an unproven window.

    This helper matches on the process (code.exe), then WAITS on a condition
    (the real foreground process) instead of sleeping. It returns False when the
    foreground could not be proven, so the caller can refuse to act.
    """
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    found: list[int] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _cb(hwnd, _lparam):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            if _window_process_name(hwnd).lower() in (
                "code.exe", "code - insiders.exe"
            ):
                found.append(int(hwnd))
                return False
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception as e:
        vlog(f"copy_reply_latest: EnumWindows failed ({e})")

    if found:
        try:
            u.ShowWindow(found[0], 9)  # SW_RESTORE
            u.BringWindowToTop(found[0])
            u.SetForegroundWindow(found[0])
        except Exception as e:
            vlog(f"copy_reply_latest: SetForegroundWindow failed ({e})")
    else:
        vlog("copy_reply_latest: no code.exe window found to activate")

    # CONDITION WAIT: prove the foreground rather than sleeping a fixed 0.4s.
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: _foreground_is_code(),
            timeout=timeout, description="VS Code is foreground",
        )
        return True
    except cbw.ConditionTimeout as e:
        vlog(f"copy_reply_latest: foreground NOT proven ({e})")
        return False


def _window_process_name(hwnd: int) -> str:
    """Process image name owning `hwnd`, or '' when unknown."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    try:
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
        finally:
            k.CloseHandle(h)
    except Exception:
        pass
    return ""


def _foreground_is_code() -> bool:
    """True when the FOREGROUND window belongs to VS Code.

    Checks the process name first (authoritative) and falls back to the title
    only as a secondary signal.
    """
    import ctypes

    u = ctypes.windll.user32
    try:
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        proc = _window_process_name(hwnd).lower()
        if proc in ("code.exe", "code - insiders.exe"):
            return True
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        return "visual studio code" in buf.value.lower()
    except Exception:
        return False


def copy_reply_latest(require_session_id: bool = True) -> str:
    """LATEST copy method (user 2026-09-19): use VS Code's native
    'Copy Final Response' command (workbench.action.chat.copyFinalResponse,
    bound to Ctrl+Alt+C) to copy the LATEST assistant reply straight to the
    clipboard — no icon hover/click, no vision. Far more reliable than the
    icon-click path.

    The command only fires when the CHAT view has focus. When this script is
    launched from the integrated terminal (or AHK), the terminal/editor may
    hold focus, so a bare Ctrl+Alt+C would be swallowed. Fix: activate
    Code.exe, then CLICK the chat panel to move focus into the chat webview,
    then send Ctrl+Alt+C. Raises if the clipboard didn't change or has no
    SESSION_ID (caller falls back to the icon-click 'not latest' method).

    require_session_id=False (used by the reverse tool ^!d, which only wants
    the clipboard text): skip the SESSION_ID parse and return "" on success.
    """
    import ctypes
    import time
    import pyautogui

    pyautogui.FAILSAFE = False

    # 1) Make sure VS Code is the foreground window.
    #
    # FIXED 2026-09-20 (P2-window): this used
    # `FindWindowW(None, "Visual Studio Code")`, which matches on the window
    # TITLE. VS Code's title carries the open file name and changes constantly,
    # so the call was measured to return 0 — and when it returned 0 the code
    # SILENTLY skipped activation and clicked anyway. A silent no-op followed by
    # an action on an unproven window is the defect class this workset removes.
    #
    # Activation is now by PROCESS (code.exe) and the result is PROVEN by a
    # condition wait. If the foreground is never proven, we do NOT click.
    if not _activate_code_and_prove():
        raise RuntimeError(
            "copy_reply_latest: VS Code foreground could not be proven; "
            "refusing to click into an unproven window"
        )

    # 2) Click the chat panel (right side) to move focus from the terminal /
    #    editor into the chat webview. Center of the chat message area.
    sw, sh = pyautogui.size()
    cx, cy = int(0.56 * sw), int(0.40 * sh)
    pyautogui.click(cx, cy)
    vlog(f"copy_reply_latest: clicked chat panel at ({cx},{cy}) to focus chat")
    time.sleep(0.6)

    before = read_clipboard()

    # 3) Fire the native 'Copy Final Response' hotkey (Ctrl+Alt+C).
    pyautogui.hotkey("ctrl", "alt", "c")
    vlog("copy_reply_latest: sent Ctrl+Alt+C (copyFinalResponse)")
    time.sleep(0.8)

    text = read_clipboard()
    if text == before:
        # Chat may not have had focus (or no final response yet) — nudge focus
        # back to a neutral state so the icon-click fallback is not blocked.
        pyautogui.press("escape")
        vlog("copy_reply_latest: clipboard unchanged — reply may not be ready")
        raise RuntimeError("copyFinalResponse did not change clipboard")
    vlog(f"copy_reply_latest: clipboard {len(text)} chars (changed)")

    if not require_session_id:
        # reverse tool (^!d): clipboard text is all we need
        return ""

    m = SESSION_ID_RE.search(text)
    if not m:
        vlog(f"copy_reply_latest: no SESSION_ID in clipboard: {text[:200]!r}")
        raise RuntimeError("SESSION_ID not found in copied reply")
    sid = m.group(1)
    vlog(f"copy_reply_latest: SESSION_ID={sid}")
    return sid


def copy_reply() -> str:
    """Hover the reply area -> find + click the copy icon -> read clipboard
    -> extract SESSION_ID. Returns the uuid, raises on failure."""
    import pyautogui
    import time
    pyautogui.FAILSAFE = False
    # 1) hover over the LATEST assistant reply (main chat panel) to reveal the
    #    copy icon. The correct session = the one just created (it is the
    #    ACTIVE session, its reply shows in the main panel).
    #    COPY BUTTON: X is FIXED, Y DRIFTS with reply height (user 2026-09-18)
    #    -> try MULTIPLE hover sets (same X, different Y) until the icon shows.
    sw, sh = pyautogui.size()
    hover_sets = [
        (int(0.52 * sw), int(0.26 * sh)),  # (1000, 280) short reply
        (int(0.52 * sw), int(0.40 * sh)),  # (1000, 432) medium reply
        (int(0.52 * sw), int(0.52 * sh)),  # (1000, 560) long reply
    ]
    # 1b) scroll the chat to the END first (user 2026-09-18: "copy button must
    #     at the end of chat, scrolled end and find that, first time") so the
    #     LATEST reply + its copy button are in view.
    scroll_chat_to_end()
    vlog("copy_reply: scrolled chat to end")

    before = read_clipboard()

    # 1c) PRIMARY: ICON-ROW wait+locate (user 2026-09-19): the action row
    #     (4 icons + time + model, same row) ONLY exists when the reply is
    #     COMPLETE — so polling for it IS the 'chat finished' wait, and the
    #     rightmost icon in the row is the COPY button. No LLM in the loop.
    bbox = wait_for_icon_group(max_wait=180)
    if bbox is not None:
        gx = bbox[2] - 12
        gy = (bbox[1] + bbox[3]) // 2
        pyautogui.moveTo(gx, gy)
        time.sleep(1.0)
        pyautogui.click(gx, gy)
        vlog(f"copy_reply: clicked icon-row copy at ({gx},{gy})")
        time.sleep(0.8)
        text = read_clipboard()
        if text != before:
            vlog(f"copy_reply: clipboard {len(text)} chars (changed, icon-row)")
            m = SESSION_ID_RE.search(text)
            if not m:
                vlog(f"copy_reply: no SESSION_ID in clipboard: {text[:200]!r}")
                raise RuntimeError("SESSION_ID not found in copied reply")
            sid = m.group(1)
            vlog(f"copy_reply: SESSION_ID={sid}")
            record_click_success("copy_button", gx, gy)
            return sid
        vlog("copy_reply: icon-row click did NOT change clipboard — next fallback")

    # 1d) FALLBACK: user-measured COPY button (906, 793) — deterministic spot.
    #     Only used if the group locate missed. Verify the clipboard CHANGED.
    cbx, cby = 906, 793
    pyautogui.moveTo(cbx, cby)
    time.sleep(1.2)
    pyautogui.click(cbx, cby)
    vlog(f"copy_reply: clicked user-measured copy button at ({cbx},{cby})")
    time.sleep(0.8)
    text = read_clipboard()
    if text != before:
        vlog(f"copy_reply: clipboard {len(text)} chars (changed, user-measured)")
        m = SESSION_ID_RE.search(text)
        if not m:
            vlog(f"copy_reply: no SESSION_ID in clipboard: {text[:200]!r}")
            raise RuntimeError("SESSION_ID not found in copied reply")
        sid = m.group(1)
        vlog(f"copy_reply: SESSION_ID={sid}")
        record_click_success("copy_button", cbx, cby)
        return sid
    vlog("copy_reply: user-measured click did NOT change clipboard — next fallback")

    # 2) for each hover set: locate icon -> click -> VERIFY clipboard changed.
    #    A click that doesn't change the clipboard = missed the icon (LLM
    #    coords can be wrong) -> try the next hover set.
    for i, (hx, hy) in enumerate(hover_sets):
        pyautogui.moveTo(hx, hy)
        time.sleep(1.2)
        vlog(f"copy_reply: hover set {i+1}/{len(hover_sets)} at ({hx},{hy})")
        pos = diff_find_copy_icon(hx, hy)
        if pos is None:
            png = local_snapshot()
            pos = find_copy_icon(png)
            if pos is None:
                vlog(f"copy_reply: set {i+1} template match failed — LLM locate")
                pos = vision_find_copy_icon(png)
        if pos is None:
            continue
        cx, cy = pos
        # sanity bounds: copy icon lives in the reply panel (right of center,
        # below the top toolbar). LLM sometimes hallucinates coords.
        if not (900 <= cx <= 1700 and 150 <= cy <= 900):
            vlog(f"copy_reply: icon ({cx},{cy}) outside sanity bounds — skip")
            continue
        pyautogui.click(cx, cy)
        vlog(f"copy_reply: clicked copy icon at ({cx},{cy})")
        time.sleep(0.8)
        text = read_clipboard()
        if text == before:
            vlog(f"copy_reply: clipboard UNCHANGED after click — missed icon, next set")
            continue
        vlog(f"copy_reply: clipboard {len(text)} chars (changed)")
        m = SESSION_ID_RE.search(text)
        if not m:
            vlog(f"copy_reply: no SESSION_ID in clipboard: {text[:200]!r}")
            raise RuntimeError("SESSION_ID not found in copied reply")
        sid = m.group(1)
        vlog(f"copy_reply: SESSION_ID={sid}")
        record_click_success("copy_button", cx, cy)
        return sid
    raise RuntimeError("copy icon not found on any hover set (template + LLM)")


def record_chat_reply(sid: str, reply_text: str) -> None:
    """Fill the send-out row (created by f_send_chat.py) with the reply.

    UPDATEs chat_reply_log (agent.db, the existing table with SHA256
    chat_id): session_id, chat_id = sha256(session_id), reply_text,
    elapsed_ms = now - sent_at (how long the chat took to finish).
    Never raises: a DB hiccup must not break the SESSION_ID handoff.
    """
    try:
        import sqlite3
        import datetime

        agent_db = os.path.join(ROOT, "agent.db")
        row_id = None
        row_file = os.path.join(ROOT, "new_session_chat_row.txt")
        if os.path.isfile(row_file):
            try:
                row_id = int(open(row_file, encoding="utf-8").read().strip())
            except Exception as e:
                vlog(f"record_chat_reply: row id parse fail: {e}")
        chat_id = hashlib.sha256(sid.encode("utf-8")).hexdigest()
        conn = sqlite3.connect(agent_db)
        try:
            if row_id is not None:
                # fetch sent_at to compute elapsed_ms
                r = conn.execute(
                    "SELECT sent_at FROM chat_reply_log WHERE id=?",
                    (row_id,),
                ).fetchone()
                elapsed_ms = None
                if r and r[0]:
                    try:
                        sent_dt = datetime.datetime.strptime(
                            str(r[0]), "%Y-%m-%d %H:%M:%S"
                        )
                        elapsed_ms = int(
                            (datetime.datetime.now() - sent_dt)
                            .total_seconds() * 1000
                        )
                    except Exception:
                        elapsed_ms = None
                conn.execute(
                    """
                    UPDATE chat_reply_log
                    SET session_id=?, chat_id=?, reply_text=?, elapsed_ms=?
                    WHERE id=?
                    """,
                    (sid, chat_id, reply_text, elapsed_ms, row_id),
                )
                conn.commit()
                vlog(f"record_chat_reply: UPDATE row={row_id} "
                     f"chat_id={chat_id[:12]}.. elapsed_ms={elapsed_ms}")
            else:
                # no send row (send py skipped?) -> insert a fresh one
                conn.execute(
                    """
                    INSERT INTO chat_reply_log
                        (session_id, chat_id, model, reply_text, source)
                    VALUES (?, ?, ?, ?, 'f1_new_session')
                    """,
                    (sid, chat_id, "Qwen: Qwen3.8 27B", reply_text),
                )
                conn.commit()
                vlog(f"record_chat_reply: INSERT (no send row) "
                     f"chat_id={chat_id[:12]}..")
        finally:
            conn.close()
    except Exception as e:
        vlog(f"record_chat_reply: FAIL {type(e).__name__}: {e}")


def _finalize(sid: str, reply_text: str) -> None:
    """Write session_id_last.txt + record chat_reply_log + print result."""
    with open(SESSION_ID_FILE, "w", encoding="utf-8") as f:
        f.write(sid)
    try:
        record_chat_reply(sid, reply_text or f"(no-text) SESSION_ID: {sid}")
    except Exception as e:
        vlog(f"record_chat_reply skipped: {e}")
    print(f"SESSION_ID: {sid}")


def _vision_fallback() -> str:
    """LLM reads the SESSION_ID straight from the screen (last resort)."""
    png = local_snapshot()
    sid = vision_read_session_id(png)
    if not sid:
        raise RuntimeError("vision could not read SESSION_ID")
    vlog(f"vision fallback SESSION_ID={sid}")
    return sid


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: f_copy_reply.py --copy-reply | --copy-latest | "
              "--copy-only | --check")
        sys.exit(1)
    cmd = sys.argv[1]
    try:
        if cmd == "--copy-reply":
            # NOT-LATEST method: icon-click. copy_reply() waits for the ICON
            # ROW (4 icons + time + model) on hover — that row ONLY exists when
            # the reply is COMPLETE, so it IS the 'chat finished' proof.
            sid = None
            reply_text = ""
            try:
                sid = copy_reply()
                reply_text = read_clipboard()
            except Exception as e:
                vlog(f"copy_reply failed ({e}) — falling back to vision read")
            if not sid:
                sid = _vision_fallback()
            _finalize(sid, reply_text)
        elif cmd == "--copy-latest":
            # LATEST method (user 2026-09-19): VS Code native
            # 'Copy Final Response' (Ctrl+Alt+C) — copies the LATEST reply,
            # no icon click. Fall back to the icon-click method, then vision.
            sid = None
            reply_text = ""
            try:
                sid = copy_reply_latest()
                reply_text = read_clipboard()
            except Exception as e:
                vlog(f"copy_reply_latest failed ({e}) — falling back to icon-click")
            if not sid:
                try:
                    sid = copy_reply()
                    reply_text = read_clipboard()
                except Exception as e2:
                    vlog(f"copy_reply (fallback) failed ({e2}) — vision read")
            if not sid:
                sid = _vision_fallback()
            _finalize(sid, reply_text)
        elif cmd == "--copy-only":
            # REVERSE tool (^!d): copy the latest VS Code chat reply to the
            # clipboard ONLY — no SESSION_ID parse, no chat_reply_log row.
            # Used by f_deepseek_paste.py to feed the DeepSeek input box.
            copy_reply_latest(require_session_id=False)
            text = read_clipboard()
            vlog(f"copy_only: clipboard {len(text)} chars ready for paste")
            print(f"OK: copied {len(text)} chars (copy-only)")
        elif cmd == "--check":
            import cv2  # noqa: F401
            import pyautogui  # noqa: F401
            ok_tpl = os.path.isfile(COPY_TEMPLATE)
            print(f"cv2+pyautogui OK, copy_template={ok_tpl}")
            sys.exit(0 if ok_tpl else 1)
        else:
            print(f"unknown command: {cmd}")
            sys.exit(1)
    except Exception as e:
        vlog(f"FAIL {cmd}: {e}")
        print(f"FAIL: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
