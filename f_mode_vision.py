# -*- coding: utf-8 -*-
"""
Tool #6 helper: f_mode_vision.py — OpenClaw vision + Ollama to locate the
VS Code chat mode selector / dropdown item, so AHK can click it.

WHY: the old ^l/^i "native key" path was WRONG — ^l/^i are VS Code EDITOR
shortcuts (Insert Line Above/Below), not chat-mode shortcuts. They never
switched the selector and eventually killed the chat panel. This script uses
the proven F10 pattern (OpenClaw screen.snapshot + local Ollama vision) to
find the real UI element and let AHK click it.

Usage:
    python f_mode_vision.py --detect selector          # find mode selector -> "X Y"
    python f_mode_vision.py --detect item --mode PLAN  # find open dropdown item -> "X Y"
    python f_mode_vision.py --check                    # OpenClaw + Ollama reachable? exit 0/1

Output: "X Y" (screen pixel coords) on success, or "FAIL: reason" (exit 1).
Run with pythonw.exe from AHK (no console window flash).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(ROOT, "mode_vision_coords.txt")
VISION_LOG = os.path.join(ROOT, "mode_vision_log.txt")


def vlog(msg: str) -> None:
    """Append to mode_vision_log.txt (pythonw swallows stdout; AHK needs a file)."""
    import datetime
    try:
        with open(VISION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def load_env() -> None:
    env_path = os.path.join(ROOT, ".env")
    if not os.path.isfile(env_path):
        return
    for line in open(env_path, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def find_vscode_hwnd() -> tuple[int, str]:
    """Find the VS Code window handle. Returns (hwnd, title); hwnd=0 if none.

    Matching rule (fixed 2026-09-20): the window title CONTAINS
    "Visual Studio Code", rather than ENDSWITH. VS Code appends suffixes to
    the title (e.g. "- Untracked", "- Modified", or the workspace name), so an
    endswith test failed: the real title observed was
    "classify.json - agent_system - Visual Studio Code - Untracked", which made
    `--verify` raise "no Code.exe window" and broke the Ctrl+Alt+P mode hotkey.

    VS Code is Electron, so it shares the window class "Chrome_WidgetWin_1"
    with every Chrome-based browser. Those are excluded explicitly: a title
    containing a browser marker is never VS Code, even though the class matches.
    """
    import ctypes

    u = ctypes.windll.user32
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int

    # Titles matching these are NOT VS Code even though the class matches.
    BROWSER_MARKERS = (
        "google chrome",
        "microsoft edge",
        "deepseek",
        "豆包",
        "即時字幕",
    )
    # Minimum length guard: avoids matching stray/blank titled windows.
    MIN_TITLE = 20

    hwnd = u.FindWindowW("Chrome_WidgetWin_1", None)
    while hwnd:
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        t = buf.value
        low = t.lower()
        if (
            "visual studio code" in low
            and len(t) > MIN_TITLE
            and not any(m in low for m in BROWSER_MARKERS)
        ):
            return hwnd, t[:120]
        hwnd = u.FindWindowExW(None, hwnd, "Chrome_WidgetWin_1", None)
    return 0, ""


def activate_vscode() -> str:
    """Bring VS Code to foreground + maximize, RIGHT before the snapshot.
    Uses Alt-key trick to bypass Windows foreground lock.
    Returns the window title as proof."""
    import ctypes
    import time
    u = ctypes.windll.user32
    hwnd, title = find_vscode_hwnd()
    if not hwnd:
        vlog("activate_vscode: FAIL no Code.exe window found")
        return ""
    # If VS Code is ALREADY the foreground window, do NOT touch it —
    # the Alt-key trick + ShowWindow(SW_MAXIMIZE) clears the chat input.
    if u.GetForegroundWindow() == hwnd:
        vlog("activate_vscode: already foreground, skipping (preserve input)")
        return title
    # Alt-key trick: temporarily disables foreground lock
    u.keybd_event(0x12, 0, 0, None)   # Alt down
    u.ShowWindow(hwnd, 3)             # SW_MAXIMIZE
    u.SetForegroundWindow(hwnd)
    u.keybd_event(0x12, 0, 2, None)   # Alt up
    time.sleep(1.0)
    # PROOF: verify it's actually foreground now
    fg = u.GetForegroundWindow()
    fg_buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(fg, fg_buf, 512)
    vlog(f"activate_vscode: PROOF target={title!r} foreground={fg_buf.value[:80]!r} match={fg == hwnd}")
    return title


def mcp_snapshot() -> bytes:
    """Capture full screen via OpenClaw MCP screen.snapshot -> PNG bytes."""
    sys.path.insert(0, ROOT)
    import mcp_client

    cfg = mcp_client.McpConfig.from_env()
    client = mcp_client.McpClient(cfg)
    client.initialize()
    result = client.request(
        "tools/call",
        {"name": "screen.snapshot", "arguments": {"format": "png", "maxWidth": 1920}},
    )
    text = ""
    for c in result.get("content", []):
        if c.get("type") == "text":
            text += c.get("text", "")
    data = json.loads(text)
    return base64.b64decode(data["base64"])


def local_snapshot() -> bytes:
    """Capture full screen LOCALLY via pyautogui -> PNG bytes.

    Replaces mcp_snapshot() in the hotkey flow: the OpenClaw MCP
    screen.snapshot brings the OpenClaw window to the foreground on every
    call (3x per ^!p flow = 3 popups, user complaint 2026-09-18). A local
    pyautogui screenshot has NO window, is faster, and returns the same
    full-screen PNG the template/vision code expects. mcp_snapshot() is kept
    only for --check (which is meant to verify OpenClaw is up)."""
    import io
    import pyautogui
    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()  # already a PIL Image
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    return buf.getvalue()


def screen_size() -> tuple[int, int]:
    try:
        import pyautogui
        return pyautogui.size()
    except Exception:
        import ctypes
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


def vision_find(png_bytes: bytes, prompt: str) -> tuple[int, int] | None:
    """Ask local Ollama vision for an element position (0-1000 normalized)."""
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
                 "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"}},
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
    raw = out["choices"][0]["message"]["content"]
    vlog(f"vision raw reply: {raw[:300]!r}")
    # strip ```json ... ``` fences the model sometimes wraps around the reply
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        vlog(f"vision: no JSON in reply: {raw[:200]!r}")
        print(f"vision: no JSON in reply: {raw[:200]!r}")
        return None
    obj = None
    try:
        obj = json.loads(m.group(0))
        float(obj["x"]); float(obj["y"])  # validate keys exist
    except Exception:
        # 7B-VL quirk: malformed JSON, missing "x"/"y" key, etc.
        # Fallback: extract two numbers from the JSON-like blob
        nums = re.findall(r"(\d+\.?\d*)", m.group(0))
        if len(nums) >= 2:
            obj = {"x": float(nums[0]), "y": float(nums[1])}
            vlog(f"vision: fallback parse -> x={nums[0]} y={nums[1]} (raw={m.group(0)[:80]!r})")
    if obj is None:
        vlog(f"vision: bad JSON: {m.group(0)[:120]!r}")
        print(f"vision: bad JSON: {m.group(0)[:120]!r}")
        return None
    x, y = float(obj["x"]), float(obj["y"])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        print(f"vision: coords out of range: {x}, {y}")
        return None
    return int(x), int(y)


PROMPT_SELECTOR = (
    "This is a small cropped strip from the bottom of a VS Code chat input box. "
    "In it there is a row of small buttons. Find the button that shows the "
    "word 'Ask' or 'Plan' or 'Agent' with a small icon. It is immediately to "
    "the RIGHT of the '+' button, and to the LEFT of the model name (e.g. "
    "'Qwen: Qwen3.8 27B'). It is NOT the 'Medium' button and NOT the "
    "microphone icon. "
    'Return ONLY JSON: {"x": N, "y": N} in 0-1000 normalized coordinates '
    "(0 = left/top, 1000 = right/bottom). No other text."
)

PROMPT_ITEM = (
    "This is a cropped region from VS Code. A small dropdown menu is OPEN in it, "
    "listing chat modes as menu items: 'Ask', 'Plan', 'Agent'. Find the menu "
    "item that reads exactly '{mode}'. "
    'Return ONLY JSON: {"x": N, "y": N} in 0-1000 normalized coordinates '
    "(0 = left/top edge, 1000 = right/bottom edge). No other text."
)

PROMPT_VERIFY = (
    "This is a cropped strip from the bottom of a VS Code chat input box. In it "
    "there is a MODE SELECTOR button showing the current chat mode as text: "
    "'Ask', 'Plan', or 'Agent' (with a small icon). Read the text that the mode "
    "selector currently shows. "
    'Return ONLY JSON: {"mode": "Ask" or "Plan" or "Agent"}. No other text.'
)


def _crop_region(png: bytes, x0f: float, y0f: float, x1f: float, y1f: float) -> tuple[bytes, int, int, int, int]:
    """Crop a fractional region of the PNG. Returns (crop_png, x0, y0, w, h)."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(png))
    W, H = im.size
    x0, y0 = int(x0f * W), int(y0f * H)
    x1, y1 = int(x1f * W), int(y1f * H)
    crop = im.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue(), x0, y0, x1 - x0, y1 - y0


# Maximised VS Code (1920x1080): chat input toolbar row sits at y~890-935,
# x~850-1250. Crop ONLY that row so the 7B-VL cannot grab the "LLM Task
# Monitor" chip or other elements above it.
CROP_SELECTOR = (0.42, 0.80, 0.70, 0.90)
# Dropdown opens ABOVE the selector, so item crop extends upward.
CROP_ITEM = (0.40, 0.45, 0.85, 0.95)
# Fixed fallback for maximised 1920x1080 (measured from snapshot): the mode
# selector ("Agent"/"Plan"/"Ask" pill) centre. Used when vision returns a
# point outside the expected zone.
FIXED_SELECTOR = (940, 910)
SELECTOR_ZONE = (850, 880, 1050, 940)  # x0, y0, x1, y1 sanity box

# --- Deterministic template matching (preferred over 7B-VL) ---------------
# The toolbar y-position SHIFTS with chat content height, so fixed coords fail.
# The '+' button icon is stable -> template-match it, then compute everything
# arithmetically. Offsets measured from a confirmed dropdown screenshot
# (2026-09-18, action->confirm loop):
#   plus center (880,910) -> pill center (955,910)  = plus + (75, 0)
#   dropdown opens ABOVE the selector, item rows (top->bottom):
#   Agent y=755, Ask y=790, Plan y=825  (row spacing ~35px)
PLUS_TEMPLATE = os.path.join(ROOT, "mode_plus_template.png")
PLUS_TO_PILL = (75, 0)
ITEM_OFFSET = {"PLAN": (0, -85), "ASK": (0, -120), "AGENT": (0, -155)}
# send button (↑) center (1544,912) vs pill center (955,910) = pill + (589, 2)
# measured 2026-09-18 from a confirmed maximized snapshot.
PILL_TO_SEND = (589, 2)


def find_plus(png: bytes) -> tuple[int, int] | None:
    """Template-match the stable '+' button in the chat input toolbar.
    Returns the plus center (px, py) in screen coords, or None."""
    import cv2
    import numpy as np
    import io
    from PIL import Image
    if not os.path.isfile(PLUS_TEMPLATE):
        vlog("find_plus: template file missing")
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(PLUS_TEMPLATE, cv2.IMREAD_COLOR)
    if tpl is None:
        vlog("find_plus: could not read template")
        return None
    H, W = img.shape[:2]
    # search only the bottom-right region where the chat toolbar lives
    x0, y0 = int(0.30 * W), int(0.55 * H)
    roi = img[y0:, x0:]
    res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
    minv, maxv, minl, maxl = cv2.minMaxLoc(res)
    if maxv < 0.85:
        vlog(f"find_plus: best score {maxv:.3f} < 0.85 -> not found")
        return None
    th, tw = tpl.shape[:2]
    cx = x0 + maxl[0] + tw // 2
    cy = y0 + maxl[1] + th // 2
    vlog(f"find_plus: score={maxv:.3f} center=({cx},{cy})")
    return cx, cy


def item_from_anchor(anchor: tuple[int, int], mode: str) -> tuple[int, int]:
    """Compute the dropdown item position arithmetically from the selector
    anchor. NO snapshot, NO re-activation — re-activating VS Code steals focus
    and CLOSES the open dropdown before the item click can land.
    Offsets measured from a confirmed dropdown screenshot (2026-09-18):
    dropdown opens ABOVE the selector; rows top->bottom Agent/Ask/Plan.
    anchor = selector pill center (sx, sy)."""
    sx, sy = anchor
    off = ITEM_OFFSET.get(mode.upper(), ITEM_OFFSET["PLAN"])
    px = sx + off[0]
    py = sy + off[1]
    vlog(f"item_from_anchor {mode}: anchor=({sx},{sy}) off={off} -> item=({px},{py})")
    return px, py


def find_send_button(im) -> tuple[int, int] | None:
    """Dynamically locate the send button (↑) in the chat input toolbar.

    ANCHOR = the mic icon (🎤). It is ALWAYS present (idle AND running
    states) at a stable x (~1509 on 1920x1080). The send/stop button is
    the icon immediately to its RIGHT: button = mic_center + 35 px
    (measured: mic 1509 -> send 1544).

    The old right-border method was FLAKY: the input box border x shifts
    with the model name / running state (found 1635 during an ASK flow ->
    wrong button 1609). The mic is the stable landmark.

    Mic signature (column profile over y=by-7..by+8, the ICON ROW only —
    a wider band catches input TEXT above the toolbar): a TALL run of
    >=10 columns with >=4 non-bg px (the capsule). The mic is the
    RIGHTMOST such run (text blobs are to its left). Returns (bx, by)
    or None."""
    px = im.load()
    W, H = im.width, im.height
    by = int(0.845 * H)  # toolbar row y (measured ~912 on 1080)
    x0, x1 = int(0.60 * W), int(0.95 * W)
    # column profile: count non-bg pixels per column in the icon row band
    prof = []
    for x in range(x0, x1):
        c = 0
        for y in range(by - 7, by + 9):
            if 0 <= y < H:
                r, g, b = px[x, y]
                if (r > 50 and g > 50 and b > 50) or (b - r > 25):
                    c += 1
        prof.append(c)
    n = len(prof)
    # collect all tall runs, keep the RIGHTMOST (mic is right of all text)
    runs = []
    i = 0
    while i < n:
        if prof[i] >= 4:  # start of a tall run
            j = i
            while j < n and prof[j] >= 4:
                j += 1
            # mic capsule: tall AND narrow (10-30 cols). A wide run (>30) is
            # the blue send button / input border blob, NOT the mic — the
            # rightmost-run heuristic grabbed it (1539-1823) and mis-located
            # the button at 1716 (2026-09-18 ASK send bug).
            if 10 <= (j - i) <= 30:
                runs.append((i, j))
            i = j
        else:
            i += 1
    if not runs:
        vlog("find_send_button: mic icon not found")
        return None
    i, j = runs[-1]
    mic_x = x0 + (i + j) // 2
    bx = mic_x + 35
    vlog(f"find_send_button: mic_x={mic_x} (runs={[(x0+a, x0+b-1) for a, b in runs]}) -> btn=({bx},{by})")
    return bx, by


def send_status(anchor: tuple[int, int]) -> tuple[int, int, str]:
    """Locate the send button (↑) DYNAMICALLY and sample its color to
    determine readiness. Returns (x, y, status) where status is "READY"
    (blue) or "NOT_READY" (gray). NO re-activation."""
    import io
    from PIL import Image
    png = local_snapshot()
    im = Image.open(io.BytesIO(png)).convert("RGB")
    px = im.load()
    loc = find_send_button(im)
    if loc is None:
        # fallback to the fixed offset if border detection fails
        bx = anchor[0] + PILL_TO_SEND[0]
        by = anchor[1] + PILL_TO_SEND[1]
        vlog(f"send_status: border detect failed, fallback btn=({bx},{by})")
    else:
        bx, by = loc
    # COUNT blue pixels in a tight patch around the button center. The button
    # is a filled circle: blue (ready) vs gray (not ready). The white ↑ glyph
    # is inside, so we count the blue ring, not the brightest pixel.
    blue = 0
    gray = 0
    for dy in range(-8, 9):
        for dx in range(-8, 9):
            x, y = bx + dx, by + dy
            if 0 <= x < im.width and 0 <= y < im.height:
                r, g, b = px[x, y]
                # Clean discriminator: b - r (blue dominance over red).
                # Blue core (50,93,125) b-r=75, edge (91,125,140) b-r=49.
                # Gray (61,62,63) b-r=2, white glyph (220,221,222) b-r=2.
                if b - r > 40:
                    blue += 1
                elif 40 <= r <= 90 and abs(r - g) < 12 and abs(g - b) < 12:
                    gray += 1
    status = "READY" if blue > 20 else "NOT_READY"
    vlog(f"send_status: btn=({bx},{by}) blue_px={blue} gray_px={gray} -> {status}")
    return bx, by, status


def detect(target: str, mode: str, anchor: tuple[int, int] | None = None) -> tuple[int, int]:
    vlog(f"detect {target} mode={mode} anchor={anchor}: snapshot start")
    title = activate_vscode()
    if not title:
        raise RuntimeError("activate_vscode failed: no Code.exe window")
    png = local_snapshot()
    vlog(f"detect {target}: snapshot ok ({len(png)} bytes)")
    try:
        with open(os.path.join(ROOT, "mode_vision_snapshot.png"), "wb") as f:
            f.write(png)
    except Exception:
        pass
    sw, sh = screen_size()
    vlog(f"detect {target}: screen {sw}x{sh}")

    # --- Preferred: deterministic template matching on the '+' button ---
    plus = find_plus(png)
    if plus is not None:
        pcx, pcy = plus
        if target == "selector":
            px = pcx + PLUS_TO_PILL[0]
            py = pcy + PLUS_TO_PILL[1]
            vlog(f"detect selector: TEMPLATE plus=({pcx},{pcy}) -> pill=({px},{py})")
            return px, py
        else:
            off = ITEM_OFFSET.get(mode.upper(), ITEM_OFFSET["PLAN"])
            px = pcx + PLUS_TO_PILL[0] + off[0]
            py = pcy + PLUS_TO_PILL[1] + off[1]
            vlog(f"detect item {mode}: TEMPLATE plus=({pcx},{pcy}) -> item=({px},{py})")
            return px, py

    # --- Fallback: 7B-VL vision (only when template match fails) ---
    vlog(f"detect {target}: template match FAILED -> vision fallback")
    if target == "selector":
        crop_png, cx0, cy0, cw, ch = _crop_region(png, *CROP_SELECTOR)
        norm = vision_find(crop_png, PROMPT_SELECTOR)
    else:
        if anchor:
            ax, ay = anchor
            x0f, y0f = max(0.0, (ax - 280) / sw), max(0.0, (ay - 340) / sh)
            x1f, y1f = min(1.0, (ax + 280) / sw), min(1.0, (ay - 20) / sh)
            crop_box = (x0f, y0f, x1f, y1f)
        else:
            crop_box = CROP_ITEM
        crop_png, cx0, cy0, cw, ch = _crop_region(png, *crop_box)
        # .replace not .format: the prompt contains literal {"x": N, "y": N}
        # braces which str.format() would parse as a field -> KeyError '"x"'
        norm = vision_find(crop_png, PROMPT_ITEM.replace("{mode}", mode))
    if norm is None:
        raise RuntimeError("vision detection returned no coords")
    nx, ny = norm
    px = cx0 + int(nx / 1000 * cw)
    py = cy0 + int(ny / 1000 * ch)
    vlog(f"detect {target}: crop=({cx0},{cy0},{cw}x{ch}) norm=({nx},{ny}) -> px=({px},{py})")
    if target == "selector":
        zx0, zy0, zx1, zy1 = SELECTOR_ZONE
        if not (zx0 <= px <= zx1 and zy0 <= py <= zy1):
            vlog(f"detect selector: px=({px},{py}) OUTSIDE zone {SELECTOR_ZONE} -> fixed fallback {FIXED_SELECTOR}")
            return FIXED_SELECTOR
    return px, py


def verify() -> str:
    """Snapshot + vision: read what the mode selector currently shows.
    Returns the mode word (Ask/Plan/Agent) or raises."""
    title = activate_vscode()
    if not title:
        raise RuntimeError("activate_vscode failed: no Code.exe window")
    png = local_snapshot()
    vlog("verify: snapshot ok, locating selector via template")
    # Crop the pill region using the template-matched '+' position (robust to
    # toolbar y-shift). Fallback to the fixed fractional crop if no match.
    plus = find_plus(png)
    if plus is not None:
        pcx, pcy = plus
        px = pcx + PLUS_TO_PILL[0]
        py = pcy + PLUS_TO_PILL[1]
        sw, sh = screen_size()
        x0f, y0f = max(0.0, (px - 60) / sw), max(0.0, (py - 22) / sh)
        x1f, y1f = min(1.0, (px + 60) / sw), min(1.0, (py + 22) / sh)
        vlog(f"verify: template pill=({px},{py}) crop=({x0f:.3f},{y0f:.3f},{x1f:.3f},{y1f:.3f})")
        png, _, _, _, _ = _crop_region(png, x0f, y0f, x1f, y1f)
    else:
        vlog("verify: template match failed -> fixed crop fallback")
        png, _, _, _, _ = _crop_region(png, *CROP_SELECTOR)
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT_VERIFY},
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
    raw = out["choices"][0]["message"]["content"]
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        raise RuntimeError(f"verify: no JSON in reply: {raw[:200]!r}")
    obj = json.loads(m.group(0))
    mode = str(obj.get("mode", "")).strip().upper()
    if mode not in ("ASK", "PLAN", "AGENT"):
        raise RuntimeError(f"verify: unrecognized mode: {obj.get('mode')!r}")
    return mode


def read_pill() -> str:
    """Read the mode pill WITHOUT activating or stealing focus.

    Returns "ASK" | "PLAN" | "AGENT", or "UNKNOWN" when the read cannot be
    trusted. Never raises for an UNKNOWN outcome — UNKNOWN is a real result,
    and callers MUST treat it as "no information", never as a mode.

    Unlike verify(), this NEVER calls activate_vscode(): re-activating steals
    focus (and ShowWindow(SW_MAXIMIZE) would disrupt the user). It is therefore
    safe to call from a background loop.

    Preconditions (all must hold, else UNKNOWN):
      1. A VS Code window exists AND is already the foreground window.
         If VS Code is not foreground, the screenshot could capture a
         DIFFERENT application and the VL model could hallucinate a pill —
         writing that would be worse than a stale value.
      2. find_plus() template-matches the chat toolbar '+' button, proving the
         chat input is actually visible on screen.
      3. The 7B-VL returns exactly one of ASK / PLAN / AGENT.
    """
    import ctypes

    hwnd, _title = find_vscode_hwnd()
    if not hwnd:
        vlog("read_pill: UNKNOWN (no VS Code window)")
        return "UNKNOWN"
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        vlog("read_pill: UNKNOWN (VS Code not foreground)")
        return "UNKNOWN"

    png = local_snapshot()
    plus = find_plus(png)
    if plus is None:
        vlog("read_pill: UNKNOWN (chat toolbar '+' not found — input not visible)")
        return "UNKNOWN"

    pcx, pcy = plus
    px = pcx + PLUS_TO_PILL[0]
    py = pcy + PLUS_TO_PILL[1]
    sw, sh = screen_size()
    x0f, y0f = max(0.0, (px - 60) / sw), max(0.0, (py - 22) / sh)
    x1f, y1f = min(1.0, (px + 60) / sw), min(1.0, (py + 22) / sh)
    try:
        crop, *_ = _crop_region(png, x0f, y0f, x1f, y1f)
    except Exception as e:
        vlog(f"read_pill: UNKNOWN (crop failed: {e})")
        return "UNKNOWN"

    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT_VERIFY},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{base64.b64encode(crop).decode()}"}},
            ],
        }],
        "temperature": 0.0,
    }
    try:
        req = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
        raw = out["choices"][0]["message"]["content"]
    except Exception as e:
        vlog(f"read_pill: UNKNOWN (model call failed: {type(e).__name__}: {e})")
        return "UNKNOWN"

    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        vlog(f"read_pill: UNKNOWN (no JSON in reply: {raw[:120]!r})")
        return "UNKNOWN"
    try:
        mode = str(json.loads(m.group(0)).get("mode", "")).strip().upper()
    except Exception:
        vlog(f"read_pill: UNKNOWN (bad JSON: {m.group(0)[:120]!r})")
        return "UNKNOWN"
    if mode not in ("ASK", "PLAN", "AGENT"):
        vlog(f"read_pill: UNKNOWN (unrecognized mode {mode!r})")
        return "UNKNOWN"
    vlog(f"read_pill: {mode}")
    return mode


def main() -> None:
    load_env()
    args = sys.argv[1:]

    if "--read-pill" in args:
        # Focus-free read for the reconcile loop. ALWAYS exit 0: "UNKNOWN" is a
        # valid outcome (no information), not an error. Callers must not treat
        # UNKNOWN as a mode.
        print(read_pill())
        sys.exit(0)

    if "--verify" in args:
        try:
            mode = verify()
            with open(os.path.join(ROOT, "mode_vision_verify.txt"), "w", encoding="utf-8") as f:
                f.write(mode)
            print(mode)
            sys.exit(0)
        except Exception as e:
            print(f"FAIL: {type(e).__name__}: {e}")
            sys.exit(1)

    if "--from-anchor" in args:
        # Compute item position arithmetically from the selector anchor.
        # NO snapshot / NO activate_vscode (re-activation closes the dropdown).
        i = args.index("--from-anchor")
        if i + 3 < len(args):
            ax, ay = int(args[i + 1]), int(args[i + 2])
            amode = args[i + 3].upper()
            try:
                x, y = item_from_anchor((ax, ay), amode)
                with open(COORDS_FILE, "w", encoding="utf-8") as f:
                    f.write(f"{x} {y}")
                vlog(f"OK: item {amode} (from-anchor) -> {x} {y}")
                print(f"{x} {y}")
                sys.exit(0)
            except Exception as e:
                vlog(f"FAIL: {type(e).__name__}: {e}")
                print(f"FAIL: {type(e).__name__}: {e}")
                sys.exit(1)
        print("FAIL: --from-anchor needs X Y MODE")
        sys.exit(1)

    if "--send" in args:
        # Locate the send button from the pill anchor + sample its color for
        # readiness. Writes "X Y STATUS" to mode_vision_coords.txt.
        i = args.index("--send")
        if i + 2 < len(args):
            ax, ay = int(args[i + 1]), int(args[i + 2])
            try:
                x, y, status = send_status((ax, ay))
                with open(COORDS_FILE, "w", encoding="utf-8") as f:
                    f.write(f"{x} {y} {status}")
                vlog(f"OK: send btn -> {x} {y} {status}")
                print(f"{x} {y} {status}")
                sys.exit(0)
            except Exception as e:
                vlog(f"FAIL: {type(e).__name__}: {e}")
                print(f"FAIL: {type(e).__name__}: {e}")
                sys.exit(1)
        print("FAIL: --send needs X Y (pill anchor)")
        sys.exit(1)

    if "--check" in args:
        ok = True
        try:
            mcp_snapshot()
        except Exception as e:
            ok = False
            print(f"FAIL: openclaw snapshot: {type(e).__name__}: {e}")
        if ok:
            try:
                base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
                urllib.request.urlopen(base + "/api/tags", timeout=10)
            except Exception as e:
                ok = False
                print(f"FAIL: ollama: {type(e).__name__}: {e}")
        if ok:
            print("OK")
        sys.exit(0 if ok else 1)

    target = "selector"
    mode = "PLAN"
    anchor = None
    if "--detect" in args:
        i = args.index("--detect")
        if i + 1 < len(args):
            target = args[i + 1]
    if "--mode" in args:
        i = args.index("--mode")
        if i + 1 < len(args):
            mode = args[i + 1].upper()
    if "--anchor" in args:
        i = args.index("--anchor")
        if i + 2 < len(args):
            anchor = (int(args[i + 1]), int(args[i + 2]))

    try:
        x, y = detect(target, mode, anchor)
        with open(COORDS_FILE, "w", encoding="utf-8") as f:
            f.write(f"{x} {y}")
        vlog(f"OK: {target} -> {x} {y}")
        print(f"{x} {y}")
        sys.exit(0)
    except Exception as e:
        vlog(f"FAIL: {type(e).__name__}: {e}")
        print(f"FAIL: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
