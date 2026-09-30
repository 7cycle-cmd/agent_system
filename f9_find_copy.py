# f9_find_copy.py
"""Find DeepSeek Copy button X,Y via OpenClaw screen.snapshot + local Ollama vision.

Writes "X Y" to f9_coords.txt (screen pixel coordinates).
Falls back to hardcoded coords on any failure.

Usage:
    python f9_find_copy.py            # detect, write f9_coords.txt
    python f9_find_copy.py --fallback # just write hardcoded coords
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FALLBACK_XY = "523 827"
COORDS_FILE = ROOT / "f9_coords.txt"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def mcp_snapshot() -> bytes:
    """Capture full screen via OpenClaw MCP screen.snapshot, return PNG bytes."""
    sys.path.insert(0, str(ROOT))
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


def vision_find(png_bytes: bytes) -> tuple[int, int] | None:
    """Ask local Ollama vision model for copy button position (0-1000 normalized)."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803").rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "180"))

    prompt = (
        "This is a screenshot of a DeepSeek chat page, scrolled to the bottom. "
        "The LATEST AI reply is the last message at the BOTTOM of the chat area "
        "(just above the 'Message DeepSeek' input box). "
        "Find the copy button icon (two overlapping pages/squares) that appears "
        "next to that latest AI reply at the bottom. "
        "Ignore copy buttons belonging to older messages higher up. "
        'Return ONLY JSON: {"x": N, "y": N} where x,y are in 0-1000 normalized '
        "coordinates (0 = left/top edge, 1000 = right/bottom edge of the image). "
        "No other text."
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
                        },
                    },
                ],
            }
        ],
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
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        print(f"vision: no JSON in reply: {raw[:200]!r}")
        return None
    obj = json.loads(m.group(0))
    x, y = float(obj["x"]), float(obj["y"])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        print(f"vision: coords out of 0-1000 range: {x}, {y}")
        return None
    return int(x), int(y)


def screen_size() -> tuple[int, int]:
    try:
        import pyautogui

        return pyautogui.size()
    except Exception:
        # fallback: Windows GetSystemMetrics
        import ctypes

        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def main() -> None:
    if "--fallback" in sys.argv:
        COORDS_FILE.write_text(FALLBACK_XY, encoding="utf-8")
        print(FALLBACK_XY)
        return

    load_env()
    try:
        png = mcp_snapshot()
        norm = vision_find(png)
        if norm is None:
            raise RuntimeError("vision detection failed")
        nx, ny = norm
        sw, sh = screen_size()
        x = int(nx / 1000 * sw)
        y = int(ny / 1000 * sh)
        COORDS_FILE.write_text(f"{x} {y}", encoding="utf-8")
        print(f"{x} {y}")
    except Exception as e:
        print(f"fallback: {type(e).__name__}: {e}")
        COORDS_FILE.write_text(FALLBACK_XY, encoding="utf-8")
        print(FALLBACK_XY)


if __name__ == "__main__":
    main()
