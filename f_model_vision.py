# -*- coding: utf-8 -*-
"""
Tool #7 helper: f_model_vision.py — locate the VS Code chat MODEL pill and
select a model from its dropdown, so AHK can click it.

Same proven pattern as f_mode_vision.py (OpenClaw screen.snapshot + local
Ollama vision + deterministic template matching).

WHY a separate tool: the model list is DYNAMIC (models change / get added),
so fixed row offsets (like the mode tool's ITEM_OFFSET) do NOT work. We
template-match the stable sparkle icon (✨) at the LEFT edge of the model
pill to find the pill, then use vision to read the OPEN dropdown and find
the named model row.

Usage:
    python f_model_vision.py --detect model            # find model pill -> "X Y"
    python f_model_vision.py --find-model "Qwen: Qwen3.8 27B"
                                                          # open dropdown row -> "X Y"
    python f_model_vision.py --verify                   # read current model name
    python f_model_vision.py --check                    # OpenClaw + Ollama ok? exit 0/1

Output: "X Y" (screen pixel coords) on success, or "FAIL: reason" (exit 1).
Run with pythonw.exe from AHK (no console window flash).

CRITICAL (same rule as the mode tool): --find-model takes a snapshot but does
NOT call activate_vscode() — re-activating VS Code steals focus and CLOSES the
open dropdown before the row click can land.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(ROOT, "model_vision_coords.txt")
VERIFY_FILE = os.path.join(ROOT, "model_vision_verify.txt")
VISION_LOG = os.path.join(ROOT, "model_vision_log.txt")


def vlog(msg: str) -> None:
    """Append to model_vision_log.txt (pythonw swallows stdout; AHK needs a file)."""
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


def mcp_snapshot() -> bytes:
    """Capture full screen via OpenClaw MCP screen.snapshot -> PNG bytes.
    Does NOT activate VS Code (safe to call while a dropdown is open)."""
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
    call (popups during ^!m flow, user complaint 2026-09-18). A local
    pyautogui screenshot has NO window, is faster, and returns the same
    full-screen PNG. mcp_snapshot() is kept only for --check."""
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
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        vlog(f"vision: no JSON in reply: {raw[:200]!r}")
        print(f"vision: no JSON in reply: {raw[:200]!r}")
        return None
    obj = None
    try:
        obj = json.loads(m.group(0))
        float(obj["x"]); float(obj["y"])
    except Exception:
        nums = re.findall(r"(\d+\.?\d*)", m.group(0))
        if len(nums) >= 2:
            obj = {"x": float(nums[0]), "y": float(nums[1])}
            vlog(f"vision: fallback parse -> x={nums[0]} y={nums[1]}")
    if obj is None:
        vlog(f"vision: bad JSON: {m.group(0)[:120]!r}")
        print(f"vision: bad JSON: {m.group(0)[:120]!r}")
        return None
    x, y = float(obj["x"]), float(obj["y"])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        print(f"vision: coords out of range: {x}, {y}")
        return None
    return int(x), int(y)


def vision_read_model(png_bytes: bytes) -> str | None:
    """Ask local Ollama vision to READ the current model name from the pill."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT_VERIFY_MODEL},
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
    vlog(f"verify raw reply: {raw[:300]!r}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        vlog(f"verify: no JSON in reply: {raw[:200]!r}")
        return None
    try:
        obj = json.loads(m.group(0))
        name = str(obj.get("model", "")).strip()
        return name or None
    except Exception:
        vlog(f"verify: bad JSON: {m.group(0)[:120]!r}")
        return None


PROMPT_FIND_MODEL = (
    "This is a cropped region from VS Code. A small dropdown menu is OPEN in it, "
    "listing AI models as menu items (e.g. 'Auto', 'DeepSeek: ...', "
    "'MoonshotAI: Kimi K2.5', 'Qwen: Qwen3.8 27B', 'Claude ...', 'GPT-...'). "
    "Find the menu item whose text contains '{model}'. "
    'Return ONLY JSON: {"x": N, "y": N} in 0-1000 normalized coordinates '
    "(0 = left/top edge, 1000 = right/bottom edge). No other text."
)

PROMPT_VERIFY_MODEL = (
    "This is a small cropped strip from the bottom of a VS Code chat input box. "
    "In it there is a MODEL SELECTOR button showing the current AI model name "
    "as text (e.g. 'Qwen: Qwen3.8 27B'), with a small sparkle icon to its left. "
    "Read the exact model name text that the model selector currently shows. "
    'Return ONLY JSON: {"model": "<the exact model name text>"}. No other text.'
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


# --- Deterministic template matching on the sparkle icon (✨) ---------------
# The sparkle icon is the STABLE left edge of the model pill. The pill text
# ("Qwen: Qwen3.8 27B") changes with the selected model, but the sparkle icon
# stays put. Template-match it, then compute the pill center arithmetically.
# Measured 2026-09-18 (maximized 1920x1080):
#   sparkle center (1002, 912) -> model pill center (1075, 912) = sparkle + (73, 0)
SPARKLE_TEMPLATE = os.path.join(ROOT, "model_sparkle_template.png")
SPARKLE_TO_PILL = (73, 0)
# Fixed fallback for maximized 1920x1080 (measured from snapshot).
FIXED_MODEL_PILL = (1075, 912)
# Dropdown opens ABOVE the model pill. Crop region covering the open dropdown
# (Auto at top ~y609 down to Other Models ~y900 on 1080).
CROP_DROPDOWN = (0.44, 0.48, 0.72, 0.86)
# Crop region for reading the current model name from the pill (toolbar row).
CROP_PILL = (0.48, 0.82, 0.66, 0.88)


def find_sparkle(png: bytes) -> tuple[int, int] | None:
    """Template-match the stable sparkle icon (✨) in the chat input toolbar.
    Returns the sparkle center (px, py) in screen coords, or None."""
    import cv2
    import numpy as np
    import io
    from PIL import Image
    if not os.path.isfile(SPARKLE_TEMPLATE):
        vlog("find_sparkle: template file missing")
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(SPARKLE_TEMPLATE, cv2.IMREAD_COLOR)
    if tpl is None:
        vlog("find_sparkle: could not read template")
        return None
    H, W = img.shape[:2]
    # search only the bottom-right region where the chat toolbar lives
    x0, y0 = int(0.30 * W), int(0.55 * H)
    roi = img[y0:, x0:]
    res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
    minv, maxv, minl, maxl = cv2.minMaxLoc(res)
    if maxv < 0.80:
        vlog(f"find_sparkle: best score {maxv:.3f} < 0.80 -> not found")
        return None
    th, tw = tpl.shape[:2]
    cx = x0 + maxl[0] + tw // 2
    cy = y0 + maxl[1] + th // 2
    vlog(f"find_sparkle: score={maxv:.3f} center=({cx},{cy})")
    return cx, cy


def detect_model_pill() -> tuple[int, int]:
    """Find the model pill center. Template-match the sparkle icon; fall back
    to the fixed maximized-1080 position. Returns (px, py)."""
    png = local_snapshot()
    vlog(f"detect model: snapshot ok ({len(png)} bytes)")
    try:
        with open(os.path.join(ROOT, "model_vision_snapshot.png"), "wb") as f:
            f.write(png)
    except Exception:
        pass
    sp = find_sparkle(png)
    if sp is not None:
        px = sp[0] + SPARKLE_TO_PILL[0]
        py = sp[1] + SPARKLE_TO_PILL[1]
        vlog(f"detect model: TEMPLATE sparkle=({sp[0]},{sp[1]}) -> pill=({px},{py})")
        return px, py
    vlog(f"detect model: template FAILED -> fixed fallback {FIXED_MODEL_PILL}")
    return FIXED_MODEL_PILL


def _fast_screenshot():
    """Fast full-screen capture via pyautogui (NOT the slow MCP snapshot).
    The MCP snapshot is too slow / can steal focus and the dropdown CLOSES
    before it lands. pyautogui.screenshot() is fast enough to catch the open
    dropdown. Returns a PIL Image."""
    import pyautogui
    pyautogui.FAILSAFE = False
    return pyautogui.screenshot()


def _detect_dropdown_rows(im) -> list[tuple[int, int]]:
    """Detect the text rows of the OPEN model dropdown deterministically.
    Returns a list of (y_start, y_end) bands, top->bottom. Uses a LOW
    brightness threshold (55) so the dimmed/disabled rows (Claude/GPT) are
    caught too. Fragments shorter than 8px are dropped (anti-aliasing noise)."""
    px = im.load()
    x0, x1 = 1000, 1520
    y0, y1 = 540, 910
    rows = []
    inrow = False
    start = 0
    for y in range(y0, y1):
        c = 0
        for x in range(x0, x1):
            r, g, b = px[x, y]
            if r > 55 and g > 55 and b > 55:
                c += 1
        found = c >= 8
        if found and not inrow:
            inrow = True
            start = y
        elif not found and inrow:
            inrow = False
            rows.append((start, y - 1))
    if inrow:
        rows.append((start, y1 - 1))
    # merge bands closer than 8px (same menu item, e.g. descenders)
    merged = []
    for s, e in rows:
        if merged and s - merged[-1][1] < 8:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # drop noise fragments (h < 8)
    merged = [(s, e) for s, e in merged if (e - s + 1) >= 8]
    return merged


def _ocr_row(im, yc: int) -> str:
    """OCR a single dropdown row (one line of text) via local Ollama vision.
    Single-line reading is reliable for the 7B model (unlike multi-row
    coordinate finding). Returns the text string (may be empty)."""
    import io
    crop = im.crop((1000, yc - 16, 1520, yc + 16))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    prompt = ("This is one line of text from a menu. Read the exact text. "
              'Return ONLY JSON: {"text": "<exact text>"}. No other text.')
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
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
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
    vlog(f"ocr row y={yc}: {raw[:120]!r}")
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if m:
        try:
            return str(json.loads(m.group(0)).get("text", "")).strip()
        except Exception:
            pass
    return raw[:80].strip()


def find_model_row(model_name: str) -> tuple[int, int]:
    """With the model dropdown OPEN, find the named model row.

    ROBUST approach (the single-shot 7B coordinate find was UNRELIABLE — it
    returned the wrong row, e.g. DeepSeek instead of Kimi):
      1. fast pyautogui screenshot (MCP snapshot is too slow, dropdown closes)
      2. detect all dropdown text rows deterministically (pixel analysis)
      3. OCR each row individually (single-line reading is reliable)
      4. match the target model name (case-insensitive substring)
      5. return the matched row's center (px, py)

    NO re-activation (re-activating VS Code steals focus and CLOSES the
    dropdown). Returns (px, py) screen coords."""
    im = _fast_screenshot()
    vlog(f"find_model '{model_name}': fast screenshot ok ({im.width}x{im.height})")
    try:
        im.save(os.path.join(ROOT, "model_vision_snapshot.png"))
    except Exception:
        pass
    rows = _detect_dropdown_rows(im)
    vlog(f"find_model '{model_name}': {len(rows)} rows detected")
    target = model_name.lower()
    for s, e in rows:
        yc = (s + e) // 2
        text = _ocr_row(im, yc)
        vlog(f"find_model: row y={yc} text={text!r}")
        if not text:
            continue
        # match: target is a substring of the row text (case-insensitive).
        # Also accept if the row text is a substring of the target (OCR may
        # drop the 'OpenRouter' suffix or the checkmark).
        if target in text.lower() or text.lower() in target:
            px = 1150  # click in the middle of the dropdown row
            vlog(f"find_model '{model_name}': MATCH row y={yc} text={text!r} -> ({px},{yc})")
            return px, yc
    raise RuntimeError(f"model '{model_name}' not found in dropdown rows")


def verify_model() -> str:
    """Snapshot + vision: read what the model selector currently shows.
    Returns the model name string or raises."""
    png = local_snapshot()
    vlog("verify: snapshot ok, reading model pill")
    crop_png, _, _, _, _ = _crop_region(png, *CROP_PILL)
    name = vision_read_model(crop_png)
    if not name:
        raise RuntimeError("verify: vision returned no model name")
    vlog(f"verify: model = {name!r}")
    return name


def main() -> None:
    load_env()
    args = sys.argv[1:]

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

    if "--verify" in args:
        try:
            name = verify_model()
            with open(VERIFY_FILE, "w", encoding="utf-8") as f:
                f.write(name)
            print(name)
            sys.exit(0)
        except Exception as e:
            print(f"FAIL: {type(e).__name__}: {e}")
            sys.exit(1)

    if "--find-model" in args:
        i = args.index("--find-model")
        if i + 1 < len(args):
            name = args[i + 1]
            try:
                x, y = find_model_row(name)
                with open(COORDS_FILE, "w", encoding="utf-8") as f:
                    f.write(f"{x} {y}")
                vlog(f"OK: find-model '{name}' -> {x} {y}")
                print(f"{x} {y}")
                sys.exit(0)
            except Exception as e:
                vlog(f"FAIL: {type(e).__name__}: {e}")
                print(f"FAIL: {type(e).__name__}: {e}")
                sys.exit(1)
        print("FAIL: --find-model needs a model name")
        sys.exit(1)

    if "--detect" in args:
        try:
            x, y = detect_model_pill()
            with open(COORDS_FILE, "w", encoding="utf-8") as f:
                f.write(f"{x} {y}")
            vlog(f"OK: model pill -> {x} {y}")
            print(f"{x} {y}")
            sys.exit(0)
        except Exception as e:
            vlog(f"FAIL: {type(e).__name__}: {e}")
            print(f"FAIL: {type(e).__name__}: {e}")
            sys.exit(1)

    print("FAIL: unknown command (use --detect / --find-model / --verify / --check)")
    sys.exit(1)


if __name__ == "__main__":
    main()
