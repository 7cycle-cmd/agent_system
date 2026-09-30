# -*- coding: utf-8 -*-
"""
Tool #9 helper: f_vision_ask.py — ask the local 7B-VL LLM a SPECIFIC
yes/no question about the current screen. One small question per step =
no blind trying.

Usage:
    python f_vision_ask.py --ask "Does the chat input box contain the text 'who a u?'? Answer YES or NO."

Writes "YES" or "NO" to vision_ask_answer.txt (for AHK to read).
Output: "YES" or "NO" (exit 1 on LLM error -> writes "ERROR").
Run with pythonw.exe from AHK (no console window flash).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ANSWER_FILE = os.path.join(ROOT, "vision_ask_answer.txt")
VISION_LOG = os.path.join(ROOT, "vision_ask_log.txt")


def vlog(msg: str) -> None:
    import datetime
    try:
        with open(VISION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def local_snapshot() -> bytes:
    """Capture full screen LOCALLY via pyautogui -> PNG bytes (no popup)."""
    import pyautogui
    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    return buf.getvalue()


def ask(png: bytes, question: str) -> str:
    """Ask the 7B-VL model a yes/no question. Returns 'YES' | 'NO' | 'ERROR'."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
    prompt = question.rstrip() + "\nAnswer with ONLY one word: YES or NO."
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
    raw = out["choices"][0]["message"]["content"]
    vlog(f"ask raw: {raw[:200]!r}")
    up = raw.upper()
    # YES wins if both appear (e.g. "YES, it does")
    if "YES" in up:
        return "YES"
    if "NO" in up:
        return "NO"
    return "ERROR"


def crop_region(png: bytes, x: int, y: int, w: int, h: int, scale: int = 2) -> bytes:
    """Crop a screen region and upscale it (small UI text is unreadable to
    7B-VL at full-screen resolution). Returns PNG bytes."""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    crop = im.crop((x, y, x + w, y + h))
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "--ask":
        print("usage: f_vision_ask.py --ask \"question\" [--region X,Y,W,H]")
        sys.exit(1)
    question = " ".join(sys.argv[2:])
    region = None
    if "--region" in sys.argv:
        i = sys.argv.index("--region")
        if i + 1 < len(sys.argv):
            try:
                region = tuple(int(v) for v in sys.argv[i + 1].split(","))
            except ValueError:
                region = None
    try:
        png = local_snapshot()
        if region:
            png = crop_region(png, *region)
            vlog(f"ask region: {region} (2x upscale)")
        ans = ask(png, question)
    except Exception as e:
        vlog(f"FAIL: {type(e).__name__}: {e}")
        ans = "ERROR"
    with open(ANSWER_FILE, "w", encoding="utf-8") as f:
        f.write(ans)
    print(ans)
    sys.exit(0 if ans in ("YES", "NO") else 1)


if __name__ == "__main__":
    main()
