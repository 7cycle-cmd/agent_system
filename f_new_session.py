# -*- coding: utf-8 -*-
"""
Tool #8 helper: f_new_session.py — locate the VS Code chat "New Session"
button (right Sessions panel) via local screenshot + OpenCV template match,
and fetch the "worker identity" paste template from the DB (format_templates).

WHY local_snapshot: mcp_snapshot() brings the OpenClaw window to the
foreground on every call (popup bug, 2026-09-18). pyautogui.screenshot()
has no window and is faster.

Template text is DB-DRIVEN: format_templates row prompt_setting_key=
'worker_identity' (id=2609). Edit it in the Prompt Setting UI; this tool
always reads the current DB value.

Usage:
    python f_new_session.py --detect          # find New Session button -> "X Y"
    python f_new_session.py --get-template    # print worker_identity template text
    python f_new_session.py --check           # deps + template file + DB row ok?

Output: "X Y" (screen pixel coords) on success, or "FAIL: reason" (exit 1).
Run with pythonw.exe from AHK (no console window flash).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(ROOT, "new_session_coords.txt")
TEMPLATE_TEXT_FILE = os.path.join(ROOT, "new_session_template_text.txt")
VISION_LOG = os.path.join(ROOT, "new_session_log.txt")
NS_TEMPLATE = os.path.join(ROOT, "new_session_template.png")
TEMPLATE_KEY = "worker_identity"
FIXED_NS_BTN = (1680, 167)  # fallback: maximized 1920x1080, right Sessions panel


def vlog(msg: str) -> None:
    """Append to new_session_log.txt (pythonw swallows stdout; AHK needs a file)."""
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


def find_new_session_btn(png: bytes) -> tuple[int, int] | None:
    """Template-match the 'New Session' button in the right Sessions panel.
    Returns the button center (px, py) in screen coords, or None."""
    import cv2
    import numpy as np
    import io
    from PIL import Image
    if not os.path.isfile(NS_TEMPLATE):
        vlog("find_new_session_btn: template file missing")
        return None
    im = Image.open(io.BytesIO(png)).convert("RGB")
    img = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(NS_TEMPLATE, cv2.IMREAD_COLOR)
    if tpl is None:
        vlog("find_new_session_btn: could not read template")
        return None
    H, W = img.shape[:2]
    # search only the top-right region where the Sessions panel lives
    x0, y0 = int(0.70 * W), 0
    roi = img[y0:y0 + int(0.30 * H), x0:]
    if tpl.shape[0] > roi.shape[0] or tpl.shape[1] > roi.shape[1]:
        vlog("find_new_session_btn: template larger than ROI")
        return None
    res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
    minv, maxv, minl, maxl = cv2.minMaxLoc(res)
    if maxv < 0.80:
        vlog(f"find_new_session_btn: best score {maxv:.3f} < 0.80 -> not found")
        return None
    th, tw = tpl.shape[:2]
    cx = x0 + maxl[0] + tw // 2
    cy = y0 + maxl[1] + th // 2
    vlog(f"find_new_session_btn: score={maxv:.3f} center=({cx},{cy})")
    return cx, cy


def detect() -> tuple[int, int]:
    """Find the New Session button center. Template match; fixed fallback."""
    png = local_snapshot()
    vlog(f"detect: snapshot ok ({len(png)} bytes)")
    try:
        with open(os.path.join(ROOT, "new_session_snapshot.png"), "wb") as f:
            f.write(png)
    except Exception:
        pass
    pos = find_new_session_btn(png)
    if pos is not None:
        vlog(f"detect: TEMPLATE -> {pos}")
        return pos
    vlog(f"detect: template FAILED -> fixed fallback {FIXED_NS_BTN}")
    return FIXED_NS_BTN


def get_active_template() -> dict:
    """MIDDLEWARE: read the active worker_identity row from the DB.

    Returns {'id','prompt','mode'} — the single source of truth the F1
    send path consumes. 'mode' is the target chat mode to switch to before
    sending (e.g. 'ask'). Edit the row in the Prompt Setting UI; this always
    reads the latest DB value. is_active=1 = default now (proof-record
    semantics = future work)."""
    sys.path.insert(0, ROOT)
    import coord_store as cs
    row = cs.get_format_template_by_key(TEMPLATE_KEY)
    if not row:
        raise RuntimeError(f"format_templates row '{TEMPLATE_KEY}' not found (is_active=1)")
    prompt = str(row.get("instruction") or "").strip()
    if not prompt:
        raise RuntimeError(f"template '{TEMPLATE_KEY}' has empty instruction")
    mode = str(row.get("mode") or "ask").strip().lower() or "ask"
    vlog(f"get_active_template: id={row['id']} key={row['prompt_setting_key']} "
         f"mode={mode} chars={len(prompt)}")
    return {"id": int(row["id"]), "prompt": prompt, "mode": mode}


def get_template_text() -> str:
    """Read the worker_identity template prompt from the DB (format_templates)."""
    return get_active_template()["prompt"]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: f_new_session.py --detect | --get-template | --check")
        sys.exit(1)
    cmd = sys.argv[1]
    try:
        if cmd == "--detect":
            x, y = detect()
            out = f"{x} {y}"
            with open(COORDS_FILE, "w", encoding="utf-8") as f:
                f.write(out)
            print(out)
        elif cmd == "--get-template":
            text = get_template_text()
            with open(TEMPLATE_TEXT_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            print(text)
        elif cmd == "--get-mode":
            mode = get_active_template()["mode"]
            with open(os.path.join(ROOT, "new_session_mode.txt"), "w", encoding="utf-8") as f:
                f.write(mode)
            print(mode)
        elif cmd == "--check":
            import cv2  # noqa: F401
            import pyautogui  # noqa: F401
            ok_tpl = os.path.isfile(NS_TEMPLATE)
            ok_db = get_template_text() is not None
            print(f"cv2+pyautogui OK, template_file={ok_tpl}, db_template={ok_db}")
            sys.exit(0 if (ok_tpl and ok_db) else 1)
        else:
            print(f"unknown command: {cmd}")
            sys.exit(1)
    except Exception as e:
        vlog(f"FAIL {cmd}: {e}")
        print(f"FAIL: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
