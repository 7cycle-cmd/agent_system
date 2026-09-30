# -*- coding: utf-8 -*-
"""f_doubao_send.py — drive the 豆包 desktop APP: prove env, click the chat
box, paste the worker_identity template, send with Enter, and prove each step.

USER SPEC (2026-09-20) — the 8 steps, in order:
  1) get X1,X2,Y1,Y2 of the 豆包 window  -> centre point (bring to front)
  2) confirm the environment is ready
  3) get X1,X2,Y1,Y2 of the 豆包 chat box -> centre point
  4) onclick in the chat box
  5) paste prompt > worker_identity (test)
  6) confirm worker_identity is placed in the chat box
  7) press Enter (the send hotkey)
  8) confirm the message was sent
  Every step is recorded in `doubao_send_log` for a full log.

WHY every step is proven and logged
-----------------------------------
This is UI automation on a live desktop, so the two hard rules apply:
  - env_task_proof: PROVE the window is foreground + maximized before clicking,
    and PROVE the target exists. Never assume. A wrong layout shifts every
    pixel, so an unproven click lands somewhere else and still "succeeds".
  - condition_based_waiting: wait on a CONDITION with a loud timeout, never a
    fixed sleep. A fixed sleep expires silently and lets the next step run
    against a UI that never became ready.

A step that cannot be proven is recorded as UNKNOWN, never as PASS. "We learned
nothing" must stay recordable, otherwise the honest outcome is the one that gets
suppressed.

Usage:
  python f_doubao_send.py --detect          # list candidate 豆包 windows
  python f_doubao_send.py --find-window     # step 1: measure window area
  python f_doubao_send.py --prove-env       # step 2: prove foreground+maximized
  python f_doubao_send.py --find-chatbox    # step 3: measure chat box area
  python f_doubao_send.py --click-chatbox   # step 4
  python f_doubao_send.py --paste           # step 5
  python f_doubao_send.py --confirm-paste   # step 6
  python f_doubao_send.py --send            # step 7
  python f_doubao_send.py --confirm-send    # step 8
  python f_doubao_send.py --run-all         # all 8 steps, logged
  python f_doubao_send.py --log             # print the log table
  python f_doubao_send.py --check           # deps + DB + template present

Exit codes: 0 ok / 1 a step failed / 2 a step was UNKNOWN (not proven).
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"
LOG_FILE = BASE / "doubao_send_log.txt"
ANSWER_FILE = BASE / "vision_ask_answer.txt"
TEMPLATE_KEY = "worker_identity"

# ---- prompt generator: the two keys WE supply (user spec 2026-09-20) ----
# The worker_identity template is only a SHAPE (it lists the fields). The
# generated prompt must carry the ACTUAL chat_id and sha256, because those are
# the two keys given by us / the system — the model does not invent them. They
# are also what verification keys off, so a template that never states them
# cannot be verified at all.
CHAT_ID_FILE = BASE / "doubao_chat_id.txt"
SHA256_FILE = BASE / "doubao_sha256.txt"
GENERATED_PROMPT_FILE = BASE / "doubao_prompt.txt"

# The placeholders inside the worker_identity template.
PH_SESSION = "{auto-generated UUID}"
PH_MODEL = "{current model name}"
PH_TASK = "{from previous message if present, else empty}"
PH_CHAT_ID = "{from previous message if present, else empty}"
PH_SHA = "{from previous message if present, else empty}"

# The 豆包 window is matched by TITLE, because the process name varies by
# install. Titles matching these are NOT 豆包 even if they contain the word.
NOT_DOUBAO = ("visual studio code", "google chrome", "microsoft edge", "deepseek")

POPUP_ID = "doubao_app"
WIN_TARGET = "doubao_window"
BOX_TARGET = "doubao_chatbox"
SEND_TARGET = "doubao_send_button"
# The TEXT strip inside the container. Needed separately because the container
# also holds the TOOLBAR, whose glyphs are always present — so judging
# "is the box empty?" on the whole container reports text forever (measured
# 2026-09-20: row_std stayed 86 after a successful send, because the container
# included the toolbar). Emptiness is judged on the text strip only.
TEXTAREA_TARGET = "doubao_textarea"

# Input kind for each measured area / step (user spec 2026-09-20). Recorded so a
# reader knows HOW to interact with an area instead of inferring it from the
# name: 'text' = a text field, 'icon' = a clickable image (e.g. the blue send
# button), 'select' = a dropdown, 'ratio' = a relative area.
INPUT_TYPES = ("text", "icon", "select", "ratio")

# Fallback chat-box centre (maximized 1920x1080, 豆包 chat panel). Only used when
# measurement fails, and the fallback is LOGGED as such — never silently.
FALLBACK_BOX = (1100, 871)


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    from datetime import datetime
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def ensure_log_table() -> None:
    """Create the full-log table if missing (idempotent)."""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS doubao_send_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT,
                step_no     INTEGER NOT NULL,
                step_name   TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'UNKNOWN'
                            CHECK (status IN ('PASS', 'FAIL', 'UNKNOWN')),
                detail      TEXT,
                x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
                cx INTEGER, cy INTEGER,
                input_type  TEXT    NOT NULL DEFAULT 'text'
                            CHECK (input_type IN ('text', 'icon', 'select', 'ratio')),
                evidence_id TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Additive migration: an existing DB (created before input_type) must
        # gain the column, because CREATE TABLE IF NOT EXISTS will not add it.
        cols = {str(r[1]) for r in c.execute(
            "PRAGMA table_info(doubao_send_log)").fetchall()}
        if "input_type" not in cols:
            c.execute(
                "ALTER TABLE doubao_send_log ADD COLUMN input_type TEXT "
                "NOT NULL DEFAULT 'text'"
            )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_doubao_send_log_run "
            "ON doubao_send_log (run_id, step_no)"
        )
        c.commit()


def record_step(
    run_id: str,
    step_no: int,
    step_name: str,
    status: str,
    detail: str = "",
    area: tuple[int, int, int, int] | None = None,
    evidence_id: str = "",
    input_type: str = "text",
) -> None:
    """Append one step to the full log. Never raises (logging must not break the run)."""
    try:
        ensure_log_table()
        x1 = y1 = x2 = y2 = cx = cy = None
        if area:
            x1, y1, x2, y2 = area
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        kind = input_type if input_type in INPUT_TYPES else "text"
        with _conn() as c:
            c.execute(
                """
                INSERT INTO doubao_send_log
                    (run_id, step_no, step_name, status, detail,
                     x1, y1, x2, y2, cx, cy, input_type, evidence_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, step_no, step_name, status, detail,
                 x1, y1, x2, y2, cx, cy, kind, evidence_id),
            )
            c.commit()
    except Exception as e:
        log(f"record_step FAIL: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# window measurement (step 1)
# ---------------------------------------------------------------------------

def _window_title(hwnd: int) -> str:
    u = ctypes.windll.user32
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(x1, y1, x2, y2) of a window in REAL screen pixels, or None."""
    u = ctypes.windll.user32
    r = wt.RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def find_doubao_windows() -> list[dict]:
    """Every visible top-level window whose title looks like 豆包.

    Returns [{hwnd, title, rect, maximized, foreground}]. Enumerating (rather
    than FindWindow on a guessed title) means the caller can SEE the candidates
    instead of trusting one guess.
    """
    u = ctypes.windll.user32
    out: list[dict] = []
    fg = int(u.GetForegroundWindow() or 0)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            title = _window_title(hwnd)
            low = title.lower()
            if "豆包" not in title:
                return True
            if any(m in low for m in NOT_DOUBAO):
                return True
            rect = _window_rect(hwnd)
            if not rect:
                return True
            out.append({
                "hwnd": int(hwnd),
                "title": title[:120],
                "rect": rect,
                "maximized": bool(u.IsZoomed(hwnd)),
                "foreground": int(hwnd) == fg,
            })
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception as e:
        log(f"find_doubao_windows FAIL: {type(e).__name__}: {e}")
    return out


def pick_doubao_window() -> dict | None:
    """The best 豆包 window: prefer foreground, then maximized, then largest."""
    wins = find_doubao_windows()
    if not wins:
        return None

    def _area(w: dict) -> int:
        x1, y1, x2, y2 = w["rect"]
        return max(0, x2 - x1) * max(0, y2 - y1)

    wins.sort(key=lambda w: (w["foreground"], w["maximized"], _area(w)), reverse=True)
    return wins[0]


def _screen_size() -> tuple[int, int]:
    u = ctypes.windll.user32
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


def _save_area(target_id: str, rect: tuple[int, int, int, int], label: str) -> None:
    """Persist the measured AREA to coords.db target_area (cx,cy derived)."""
    try:
        sys.path.insert(0, str(BASE))
        import coord_store as cs

        cs.upsert_target_area(
            target_id, popup_id=POPUP_ID, label=label,
            x1=rect[0], y1=rect[1], x2=rect[2], y2=rect[3],
        )
    except Exception as e:
        log(f"_save_area({target_id}) FAIL: {type(e).__name__}: {e}")


def _load_area(target_id: str) -> tuple[int, int, int, int] | None:
    try:
        sys.path.insert(0, str(BASE))
        import coord_store as cs

        row = cs.get_target_area(target_id, popup_id=POPUP_ID)
        if row:
            return (int(row["x1"]), int(row["y1"]), int(row["x2"]), int(row["y2"]))
    except Exception as e:
        log(f"_load_area({target_id}) FAIL: {type(e).__name__}: {e}")
    return None


# ---------------------------------------------------------------------------
# env proof (step 2)
# ---------------------------------------------------------------------------

def prove_env(hwnd: int) -> dict:
    """PROVE the window is foreground + maximized. Returns a measured report.

    env_task_proof Rule 1: a wrong layout shifts every pixel, so the click must
    not be attempted until the layout is proven. The measured values are
    returned so they can be logged, not just asserted.
    """
    u = ctypes.windll.user32
    sw, sh = _screen_size()
    rect = _window_rect(hwnd)
    fg = int(u.GetForegroundWindow() or 0)
    maximized = bool(u.IsZoomed(hwnd))
    title = _window_title(hwnd)
    ok = bool(rect) and fg == hwnd and maximized
    return {
        "ok": ok,
        "hwnd": hwnd,
        "title": title[:120],
        "foreground": fg == hwnd,
        "foreground_hwnd": fg,
        "maximized": maximized,
        "rect": rect,
        "screen": (sw, sh),
        "detail": "hwnd=%s fg=%s maximized=%s rect=%s screen=%sx%s"
                  % (hwnd, fg == hwnd, maximized, rect, sw, sh),
    }


def activate_window(hwnd: int) -> None:
    """Bring the window to front + maximize, then WAIT for the condition.

    Uses the Alt-key trick to bypass the Windows foreground lock. The wait is a
    CONDITION (foreground == hwnd), not a fixed sleep.
    """
    u = ctypes.windll.user32
    if int(u.GetForegroundWindow() or 0) == hwnd:
        log("activate_window: already foreground, skipping")
        return
    u.keybd_event(0x12, 0, 0, None)   # Alt down
    u.ShowWindow(hwnd, 3)             # SW_MAXIMIZE
    u.SetForegroundWindow(hwnd)
    u.keybd_event(0x12, 0, 2, None)   # Alt up

    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: int(ctypes.windll.user32.GetForegroundWindow() or 0) == hwnd,
            timeout=5.0, description="豆包 window is foreground",
        )
    except cbw.ConditionTimeout as e:
        log(f"activate_window WARN: {e}")


# ---------------------------------------------------------------------------
# chat box measurement (step 3)
# ---------------------------------------------------------------------------

def find_chatbox(win_rect: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """MEASURE the 豆包 chat TEXT input band, anchored on the toolbar.

    WHY NOT the f_env_prep technique
    --------------------------------
    `f_env_prep.find_input_box` scans for BRIGHT rows (> 90), which works on
    VS Code because it is DARK-themed, so a bright line means a border. 豆包 is
    LIGHT-themed: the whole panel is bright, so `> 90` matches EVERY row. That
    produced a meaningless 41px band at the screen bottom — MEASURED 2026-09-20,
    and the crop proved it was the toolbar row, not the input. The technique did
    not transfer, so brightness is replaced by a theme-independent signal.

    THE SIGNAL: row_std (horizontal variation within a row)
    ------------------------------------------------------
      - a BORDER or empty background row is UNIFORM  -> low row_std
      - a GLYPH/TEXT row varies across the row       -> high row_std
    Measured in the 豆包 panel: the toolbar (我写作/视频生成/翻译/…/mic) is a
    high-row_std band, and the TEXT input is the uniform band directly ABOVE it.

    WHY the toolbar is the anchor: it is the most reliably detectable feature
    (16px tall, spans the panel, high row_std), and the layout puts the toolbar
    BELOW the text area. So the text band is found relative to the toolbar
    instead of relative to a faint 1px border that the scan cannot see.

    Returns (x1, y1, x2, y2) in REAL screen pixels, or None when not found —
    None is reported as UNKNOWN, never replaced by a silent guess.
    """
    import numpy as np
    import pyautogui

    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()
    g = np.array(shot.convert("L"), dtype=np.int16)
    ih, iw = g.shape[:2]
    sw, sh = _screen_size()

    # Screenshots may differ in size from the real screen; convert the
    # real-space window rect into image space before scanning.
    sx = iw / float(sw or iw)
    sy = ih / float(sh or ih)
    x1, y1, x2, y2 = win_rect
    # CLAMP negatives to 0: a maximized window's rect can start at -8 (the
    # invisible resize border), and a NEGATIVE slice index wraps around to the
    # END of the array in numpy — which silently searched the wrong region and
    # returned UNKNOWN. Measured 2026-09-20.
    ix1, iy1 = max(0, int(x1 * sx)), max(0, int(y1 * sy))
    ix2, iy2 = int(x2 * sx), int(y2 * sy)
    ix2 = min(ix2, iw)
    iy2 = min(iy2, ih)

    # Restrict the horizontal strip to the CHAT PANEL (right ~60% of the
    # window): the left sidebar contains its own text and would add noise.
    px0 = int(ix1 + (ix2 - ix1) * 0.55)
    px1 = int(ix2 - (ix2 - ix1) * 0.03)
    if px1 - px0 < 100:
        log(f"find_chatbox: panel strip too narrow ({px0}..{px1})")
        return None
    strip = g[:, px0:px1]
    row_std = strip.std(axis=1)

    # ---- container edges by BORDER COLOUR ----
    # The container has a light-blue rounded outline. A border pixel has its
    # blue channel clearly above red/green, which is theme-independent and does
    # not rely on a brightness threshold. The search is restricted to the
    # container's own Y band (found from the toolbar above), because the left
    # sidebar also contains blue elements — searching the whole window height
    # matched a sidebar control at x=38 instead of the container at x=397.
    rgb = np.array(shot.convert("RGB"), dtype=np.int16)
    r_ch, g_ch, b_ch = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    blueish = (b_ch - r_ch > 12) & (b_ch - g_ch > 6) & (b_ch > 180)

    # Find GLYPH bands = runs of high row_std in the lower half of the window.
    lo = int(iy1 + (iy2 - iy1) * 0.50)
    hi = iy2 - 1
    bands: list[tuple[int, int]] = []
    for y in range(lo, hi):
        dense = row_std[y] >= 12
        if dense:
            if bands and y - bands[-1][1] <= 4:
                bands[-1] = (bands[-1][0], y)
            else:
                bands.append((y, y))
    bands = [(a, b) for a, b in bands if b - a >= 2]
    if not bands:
        log("find_chatbox: no glyph band found (threshold 12) — UNKNOWN")
        return None

    # Bands that end well above the screen bottom are inside the app; the last
    # such band is the toolbar (the Windows taskbar is excluded).
    in_window = [(a, b) for a, b in bands if b < hi - 6]
    if not in_window:
        log("find_chatbox: all glyph bands reach the screen edge — UNKNOWN")
        return None
    toolbar_top, toolbar_bottom = in_window[-1]

    # Search for the container's vertical borders ABOVE the toolbar only. The
    # toolbar glyphs are themselves blue-ish, so including them merged the left
    # and right borders into one contiguous run.
    by0 = max(0, toolbar_top - 110)
    by1 = max(by0 + 1, toolbar_top - 2)
    band = blueish[by0:by1, ix1:ix2]
    col_count = band.sum(axis=0)
    # Rounded corners: the border columns peak well below the band height, so a
    # 50% threshold rejected every column (measured 2026-09-20). 25% selects the
    # border without admitting stray anti-aliased text pixels.
    min_count = max(8, int((by1 - by0) * 0.25))
    cols = np.where(col_count >= min_count)[0]
    if len(cols) < 2:
        log(f"find_chatbox: container border columns not found "
            f"({len(cols)} candidates) — UNKNOWN")
        return None
    runs: list[tuple[int, int]] = []
    for x in cols:
        if runs and x - runs[-1][1] <= 3:
            runs[-1] = (runs[-1][0], int(x))
        else:
            runs.append((int(x), int(x)))
    # The RIGHT border is the last run. The SEND BUTTON is also a blue run
    # inside the container (measured 2026-09-20: runs (397,402), (1354,1389),
    # (1397,1402) — the middle run is the button), so runs[0]/runs[-1] are the
    # real borders and the button sits between them.
    left_run, right_run = runs[0], runs[-1]
    if right_run[0] - left_run[1] < 100:
        log(f"find_chatbox: container too narrow "
            f"({left_run}..{right_run}) — UNKNOWN")
        return None

    # ---- FULL container extent, from its horizontal borders ----
    # WHY not the text band alone: the container's BOTTOM border is below the
    # toolbar, and the send button lives in that lower strip (measured
    # 2026-09-20: button y 970..1005, text band ended at 965). Returning only
    # the text band EXCLUDED the send button, so a containment check would have
    # failed against a CORRECT measurement.
    cl = ix1 + left_run[1]
    cr = ix1 + right_run[0]
    span = max(1, cr - cl)
    hrows: list[int] = []
    for y in range(max(0, toolbar_top - 60), min(ih, toolbar_bottom + 60)):
        if int(blueish[y, cl:cr].sum()) > span * 0.8:
            hrows.append(y)
    if len(hrows) >= 2:
        cont_top, cont_bottom = hrows[0], hrows[-1]
    else:
        # Fall back to the text band plus the toolbar, and SAY SO in the log —
        # a fallback that is silent is indistinguishable from a measurement.
        cont_top = lo
        cont_bottom = toolbar_bottom
        log(f"find_chatbox: horizontal borders not found ({len(hrows)} rows) "
            f"— using text band + toolbar as the container")

    rx1 = int(cl / sx) if sx else cl
    rx2 = int(cr / sx) if sx else cr
    ry1 = int(cont_top / sy) if sy else cont_top
    ry2 = int(cont_bottom / sy) if sy else cont_bottom
    rx1 = max(x1 + 1, rx1)
    rx2 = min(x2 - 1, rx2)
    log(f"find_chatbox: FULL container ({rx1},{ry1})-({rx2},{ry2}) "
        f"| window-local cols {left_run[0]}..{right_run[1]}, "
        f"h-border rows {hrows[:3]}{'...' if len(hrows) > 3 else ''}, "
        f"toolbar {toolbar_top}..{toolbar_bottom}")

    # The TEXT strip: container top down to the toolbar. Saved separately so
    # emptiness is judged on the text alone (see TEXTAREA_TARGET's note).
    ta_bottom_img = min(cont_bottom, toolbar_top - 2)
    if ta_bottom_img - cont_top >= 10:
        ta = (rx1, int(cont_top / sy) if sy else cont_top,
              rx2, int(ta_bottom_img / sy) if sy else ta_bottom_img)
        _save_area(TEXTAREA_TARGET, ta, "豆包 text area")
        log(f"find_chatbox: text area {ta}")

    return (rx1, ry1, rx2, ry2)


def emptiness_area(box_area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The region used to judge emptiness: the TEXT strip, not the container.

    The container includes the TOOLBAR, whose glyphs never disappear, so
    judging emptiness on the container reports "has text" even after a
    successful send. Falls back to the container only when the strip was never
    measured, and that fallback is logged (a silent fallback is
    indistinguishable from a measurement).
    """
    ta = _load_area(TEXTAREA_TARGET)
    if ta:
        return ta
    log("emptiness_area: text area not measured — falling back to the container")
    return box_area


# ---------------------------------------------------------------------------
# vision proof (steps 6 and 8)
# ---------------------------------------------------------------------------

def _vision_ask(question: str, region: str = "", hwnd: int | None = None) -> str:
    """Ask the local 7B-VL a yes/no question about the CURRENT screen.

    Kept for interface compatibility. Prefer `_vision_ask_image`, which captures
    the crop in-process while the target is PROVEN foreground.

    WHY the subprocess form is unreliable here: f_vision_ask.py takes its OWN
    screenshot, so focus can move between the action and the capture. Measured
    2026-09-20: the VL was shown a crop of VS Code and correctly answered NO
    about an image that did not contain the 豆包 box.
    """
    import subprocess

    if hwnd:
        rep = prove_env(hwnd)
        if not rep["ok"]:
            activate_window(hwnd)
            rep = prove_env(hwnd)
        if not rep["ok"]:
            log(f"vision_ask: ABORT, window not foreground: {rep['detail']}")
            return "ERROR"

    try:
        if ANSWER_FILE.exists():
            ANSWER_FILE.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(BASE / "f_vision_ask.py"), "--ask", question]
    if region:
        cmd += ["--region", region]
    try:
        subprocess.run(
            cmd, cwd=str(BASE), timeout=180,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # CREATE_NO_WINDOW: this runs from a helper/CLI context and must not
            # flash a console window (learned 2026-09-20).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if ANSWER_FILE.exists():
            return ANSWER_FILE.read_text(encoding="utf-8").strip() or "ERROR"
    except Exception as e:
        log(f"vision_ask FAIL: {type(e).__name__}: {e}")
    return "ERROR"


def capture_crop(area: tuple[int, int, int, int], tag: str = "") -> bytes:
    """Capture one region as PNG bytes, IN PROCESS, and save it for review.

    The returned bytes are the SINGLE source of truth for every question asked
    about this moment. That is what makes the checks reproducible: the same
    image is judged, and a human can open the saved PNG and compare it with the
    verdict. Re-capturing per question is what let focus drift change the image
    between two questions in the same step.
    """
    import pyautogui
    import f_vision_ask as fva

    pyautogui.FAILSAFE = False
    png = fva.local_snapshot()
    crop = fva.crop_region(
        png, area[0], area[1], max(1, area[2] - area[0]), max(1, area[3] - area[1])
    )
    if tag:
        try:
            out = BASE / f"doubao_vl_{tag}.png"
            out.write_bytes(crop)
            log(f"capture_crop: wrote {out.name} for area={area}")
        except OSError:
            pass
    return crop


def _ask_image(png: bytes, question: str) -> str:
    """Ask the 7B-VL about an ALREADY-CAPTURED image (no second screenshot)."""
    try:
        import f_vision_ask as fva

        return fva.ask(png, question)
    except Exception as e:
        log(f"ask_image FAIL: {type(e).__name__}: {e}")
        return "ERROR"


# Row_std above this means the region contains GLYPHS (text), not just a
# uniform box. CALIBRATED 2026-09-20 on the 豆包 TEXT strip (doubao_textarea):
#   box EMPTY      -> max row_std 19.4
#   box WITH TEXT  -> max row_std 62.3
# 40 sits between them with ~21 margin on both sides. The earlier value of 12
# was derived from the FULL container and produced a false FAIL after a
# successful send (the container includes the toolbar, whose glyphs are always
# present, so emptiness must be judged on the text strip and not the container).
TEXT_ROW_STD = 40


def box_is_empty(area: tuple[int, int, int, int]) -> tuple[bool, float]:
    """Deterministically decide whether the input box is empty.

    WHY NOT the VL: "is this box empty?" is a question the 7B-VL answered
    inconsistently — measured 2026-09-20 it said "has text" for a box that was
    visibly empty. Emptiness is a PIXEL property, so it is measured directly:
    text produces glyph rows, and glyph rows have high horizontal variance
    (row_std). An empty box is uniform. Using the VL here made a reliable fact
    depend on an unreliable reader.

    Returns (is_empty, max_row_std) so the caller can log the measurement.
    """
    import numpy as np
    import f_vision_ask as fva

    png = fva.local_snapshot()
    crop = fva.crop_region(
        png, area[0], area[1], max(1, area[2] - area[0]), max(1, area[3] - area[1])
    )
    import io

    from PIL import Image

    g = np.array(Image.open(io.BytesIO(crop)).convert("L"), dtype=np.int16)
    if g.size == 0:
        return False, 0.0
    # Ignore the outer 2px: the container's own border is a high-contrast line.
    inner = g[2:-2, 2:-2] if g.shape[0] > 6 and g.shape[1] > 6 else g
    row_std = inner.std(axis=1)
    mx = float(row_std.max()) if row_std.size else 0.0
    return mx < TEXT_ROW_STD, mx


def _region_str(area: tuple[int, int, int, int]) -> str:
    x1, y1, x2, y2 = area
    return "%d,%d,%d,%d" % (x1, y1, max(1, x2 - x1), max(1, y2 - y1))


# SEND is a CLICK on the blue arrow button, NOT a hotkey (user spec
# 2026-09-20). The key list below is kept ONLY as a recorded fallback for a case
# where the button cannot be measured; the normal path is step7_send's
# two-level click. MEASURED: 豆包 HAS a send button (contrary to an earlier
# probe that found none, because the box was empty at the time and the button
# is hidden then).
SEND_KEYS: tuple[tuple[str, ...], ...] = (
    ("enter",),
    ("shift", "enter"),
    ("ctrl", "enter"),
)
# WHY: the 豆包 input box GROWS UPWARD as lines are added, so after pasting a
# 6-line template the top line (which contains SESSION_ID) has scrolled ABOVE
# the originally measured band. Verifying only the measured band therefore read
# the tail lines and reported a false FAIL — measured 2026-09-20.
VERIFY_UP_PX = 150


def _verify_region(area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """The region to verify: the measured box PLUS the growth area above it.

    Kept deliberately in ONE place so the confirm steps cannot drift apart.
    """
    x1, y1, x2, y2 = area
    return (x1, max(0, y1 - VERIFY_UP_PX), x2, y2)


# ---------------------------------------------------------------------------
# clipboard + input
# ---------------------------------------------------------------------------

def _set_clipboard(text: str) -> None:
    """Set the clipboard via ctypes (64-bit safe).

    The argtypes/restype declarations are REQUIRED, not cosmetic: without
    `GlobalLock.restype = c_void_p` ctypes assumes a C int (32-bit), so the
    64-bit HGLOBAL pointer is truncated and `memmove` raises
    "access violation writing 0x20". Measured 2026-09-20.
    """
    from ctypes import wintypes

    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.restype = wintypes.BOOL
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, ctypes.c_size_t(len(data)))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        p = k.GlobalLock(h)
        if not p:
            raise RuntimeError("GlobalLock failed")
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u.CloseClipboard()


def _template_text() -> str:
    """Read the worker_identity template from the DB (SSOT)."""
    sys.path.insert(0, str(BASE))
    import coord_store as cs

    row = cs.get_format_template_by_key(TEMPLATE_KEY)
    if not row:
        raise RuntimeError(f"format_templates row '{TEMPLATE_KEY}' not found")
    text = str(row.get("instruction") or "").strip()
    if not text:
        raise RuntimeError(f"template '{TEMPLATE_KEY}' has empty instruction")
    return text


# ---------------------------------------------------------------------------
# PROMPT GENERATOR (user spec 2026-09-20)
#
# The worker_identity row is a TEMPLATE: its CHAT_ID / CHAT_SHA256 rows carry
# placeholder prose, not real values. Generating the prompt means FILLING those
# two fields with the keys we supply, so:
#   - the model is told the identity it must echo, and
#   - verification can look for THOSE EXACT VALUES instead of a fuzzy word.
#
# WHY the two keys and not SESSION_ID: session_id is returned BY 豆包, so it
# cannot be known before sending and cannot be used to verify the paste. chat_id
# and sha256 are given by us / the system, so they exist BEFORE the send and are
# therefore checkable. (User spec: "the prompt chat id and chat 256 is give by
# you, you can verify by this 2 key".)
# ---------------------------------------------------------------------------

def _read_key_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def generate_identity_prompt(
    *,
    chat_id: str,
    sha256: str,
    task_id: str = "",
    model: str = "",
    session_id: str = "",
) -> str:
    """Fill the worker_identity template with the keys we supply.

    Raises ValueError when chat_id or sha256 is missing: a prompt that does not
    state the keys cannot be verified, so generating one would produce an
    unverifiable artifact that still looks legitimate.
    """
    cid = str(chat_id or "").strip() or _read_key_file(CHAT_ID_FILE)
    sha = str(sha256 or "").strip() or _read_key_file(SHA256_FILE)
    if not cid:
        raise ValueError(
            "chat_id is required (set --chat-id or write %s)" % CHAT_ID_FILE.name
        )
    if not sha:
        raise ValueError(
            "sha256 is required (set --sha256 or write %s)" % SHA256_FILE.name
        )

    text = _template_text()
    # Replace the PLACEHOLDER PROSE, not the field label. The template's
    # CHAT_ID / CHAT_SHA256 rows both use the same placeholder text, so the
    # substitution is done per LINE to keep them distinct.
    out_lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|") and "CHAT_SHA256" in line:
            line = line.replace(PH_SHA, sha)
        elif line.lstrip().startswith("|") and "CHAT_ID" in line:
            line = line.replace(PH_CHAT_ID, cid)
        elif line.lstrip().startswith("|") and "SESSION_ID" in line:
            line = line.replace(PH_SESSION, session_id or "{auto-generated UUID}")
        elif line.lstrip().startswith("|") and "MODEL" in line and model:
            line = line.replace(PH_MODEL, model)
        elif line.lstrip().startswith("|") and "TASK_ID" in line and task_id:
            line = line.replace(PH_TASK, task_id)
        out_lines.append(line)

    body = "\n".join(out_lines)
    # State the two keys explicitly as well, so the instruction is unambiguous
    # even if the template's table is reformatted later.
    return (
        body
        + "\n\nVERIFY THESE TWO KEYS ARE PRESENT:\n"
        + "CHAT_ID: %s\n" % cid
        + "CHAT_SHA256: %s\n" % sha
    )


def write_generated_prompt(text: str) -> Path:
    """Persist the generated prompt so the paste step and the reviewer agree."""
    GENERATED_PROMPT_FILE.write_text(text, encoding="utf-8")
    return GENERATED_PROMPT_FILE


def _prompt_text_for_this_run(chat_id: str = "", sha256: str = "") -> str:
    """The text to paste: a GENERATED prompt when keys are known, else the raw template.

    Falling back to the raw template is allowed (it is still a valid identity
    block) but it is LOGGED, because a prompt without the keys cannot be
    verified against them later.
    """
    try:
        text = generate_identity_prompt(chat_id=chat_id, sha256=sha256)
        write_generated_prompt(text)
        log("prompt generator: generated with chat_id + sha256")
        return text
    except Exception as e:
        log(f"prompt generator: falling back to raw template ({e})")
        return _template_text()


# ---------------------------------------------------------------------------
# the 8 steps
# ---------------------------------------------------------------------------

def step1_find_window(run_id: str) -> dict:
    win = pick_doubao_window()
    if not win:
        record_step(run_id, 1, "find_window", "FAIL",
                    "no visible window with '豆包' in the title")
        return {"ok": False, "error": "no 豆包 window found"}
    rect = win["rect"]
    _save_area(WIN_TARGET, rect, "豆包 app window")
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    detail = ("hwnd=%s title=%r rect=%s centre=(%d,%d) maximized=%s foreground=%s"
              % (win["hwnd"], win["title"], rect, cx, cy,
                 win["maximized"], win["foreground"]))
    record_step(run_id, 1, "find_window", "PASS", detail, area=rect)
    log(f"step1: {detail}")
    return {"ok": True, "hwnd": win["hwnd"], "rect": rect, "centre": (cx, cy),
            "title": win["title"]}


def step2_prove_env(run_id: str, hwnd: int) -> dict:
    rep = prove_env(hwnd)
    if not rep["ok"]:
        # Try to fix it once, then re-prove. A failed proof is never a pass.
        activate_window(hwnd)
        rep = prove_env(hwnd)
    status = "PASS" if rep["ok"] else "FAIL"
    record_step(run_id, 2, "prove_env", status, rep["detail"],
                area=rep.get("rect"))
    log(f"step2: {status} {rep['detail']}")
    return {"ok": rep["ok"], "report": rep}


def saved_area_is_valid(area: tuple[int, int, int, int] | None,
                        win_rect: tuple[int, int, int, int]) -> tuple[bool, str]:
    """Is a previously saved measurement still applicable to THIS window?

    WHY: the live blue-border scan only succeeds when the box is focused (the
    outline is faint/absent otherwise — MEASURED 2026-09-20: 8 border columns
    focused, 0 unfocused, and a run straight after a successful send returned
    "0 candidates"). So on a repeat run the box sits EMPTY and unfocused and
    cannot be re-measured at all. The layout is nevertheless stable across runs
    (measured identical (400,916,1399,1025) repeatedly), so a previously PROVEN
    measurement may be reused — but only after being CHECKED against the live
    window, because silently reusing a stale rectangle would be exactly the
    "confident wrong answer" this tool exists to avoid.
    """
    if not area:
        return False, "no saved measurement"
    ax1, ay1, ax2, ay2 = area
    if ax2 - ax1 < 100 or ay2 - ay1 < 10:
        return False, "saved area too small (%s)" % (area,)
    x1, y1, x2, y2 = win_rect
    if ax1 < x1 or ay1 < y1 or ax2 > x2 or ay2 > y2:
        return False, ("saved area %s outside the window rect %s (layout moved)"
                       % (area, win_rect))
    return True, "saved area inside the window rect, size ok"


def step3_find_chatbox(run_id: str, win_rect: tuple[int, int, int, int],
                       hwnd: int | None = None) -> dict:
    # PROVE the window is foreground BEFORE the screenshot. The scan reads a
    # live screenshot, so if another window is on top it measures THAT window
    # and reports a confident wrong answer. Measured 2026-09-20: running from a
    # terminal, the terminal was foreground and the border search found 0
    # candidates — the container was not even on screen.
    if hwnd:
        rep = prove_env(hwnd)
        if not rep["ok"]:
            activate_window(hwnd)
            rep = prove_env(hwnd)
        if not rep["ok"]:
            record_step(run_id, 3, "find_chatbox", "UNKNOWN",
                        "window not foreground/maximized, refusing to measure: "
                        + rep["detail"])
            log(f"step3: UNKNOWN env not proven — {rep['detail']}")
            return {"ok": False, "error": "env not proven before measure"}

    area = find_chatbox(win_rect)
    reused = False
    if not area:
        # REUSE PATH: the live scan fails on an unfocused/empty box, which is
        # the normal state at the start of a repeat run. Fall back to the last
        # PROVEN measurement, but only if it still validates against the live
        # window, and SAY SO in the log and the detail string.
        saved = _load_area(BOX_TARGET)
        ok, why = saved_area_is_valid(saved, win_rect)
        if ok:
            area = saved
            reused = True
            log(f"step3: live measurement failed; REUSING validated saved "
                f"container {saved} ({why})")
    if not area:
        # Fallback is LOGGED as a fallback and recorded UNKNOWN, never PASS:
        # an unmeasured box is not a proven box.
        record_step(run_id, 3, "find_chatbox", "UNKNOWN",
                    "measurement failed and no valid saved measurement; "
                    "fallback %s NOT used for a PASS" % (FALLBACK_BOX,))
        return {"ok": False, "error": "chat box not measured"}
    _save_area(BOX_TARGET, area, "豆包 chat box")
    cx, cy = (area[0] + area[2]) // 2, (area[1] + area[3]) // 2
    detail = "area=%s centre=(%d,%d)" % (area, cx, cy)
    if reused:
        detail += " (reused validated saved measurement — live scan found no border)"
    record_step(run_id, 3, "find_chatbox", "PASS", detail, area=area,
                input_type="text")
    log(f"step3: {detail}")
    return {"ok": True, "area": area, "centre": (cx, cy)}


def step4_click_chatbox(run_id: str, centre: tuple[int, int]) -> dict:
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.click(centre[0], centre[1])
    detail = "clicked (%d,%d)" % centre
    record_step(run_id, 4, "click_chatbox", "PASS", detail, input_type="text")
    log(f"step4: {detail}")
    return {"ok": True}


def step5_paste(run_id: str, chat_id: str = "", sha256: str = "") -> dict:
    import pyautogui

    pyautogui.FAILSAFE = False
    # GENERATE the prompt (template + the two keys we supply), so the paste is a
    # real identity block that can be verified, not a template of placeholders.
    try:
        text = _prompt_text_for_this_run(chat_id, sha256)
    except Exception as e:
        record_step(run_id, 5, "paste", "FAIL", f"prompt generation failed: {e}")
        return {"ok": False, "error": str(e)}
    _set_clipboard(text)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyautogui.hotkey("ctrl", "v")
    # DESELECT after pasting. Ctrl+A leaves the whole block SELECTED, and 豆包
    # pops a floating selection toolbar ("AI 搜索 / 复制 / 翻译 / 朗读 / 总结")
    # over the text. That overlay sits on top of the content and made the
    # verification OCR non-deterministic — the SAME text was read as YES on one
    # run and NO on the next. Collapsing the selection removes the overlay, so
    # the verification sees the text alone. (Measured 2026-09-20.)
    pyautogui.press("end")
    pyautogui.press("escape")
    detail = "pasted %d chars (%s)" % (
        len(text), "generated: chat_id + sha256"
        if "VERIFY THESE TWO KEYS" in text else F"template {TEMPLATE_KEY}")
    record_step(run_id, 5, "paste", "PASS", detail)
    log(f"step5: {detail}")
    return {"ok": True, "chars": len(text)}


def step6_confirm_paste(run_id: str, box_area: tuple[int, int, int, int],
                        hwnd: int | None = None,
                        chat_id: str = "", sha256: str = "") -> dict:
    """Verify the paste using the TWO KEYS WE SUPPLIED.

    Keyed on chat_id + sha256 rather than a fuzzy word: those values are given
    by us / the system, so they exist BEFORE the send and are checkable. A
    SESSION_ID check is weaker — session_id comes back FROM 豆包, so it is not
    known at paste time and cannot prove the paste carried our identity.
    """
    cid = str(chat_id or "").strip() or _read_key_file(CHAT_ID_FILE)
    sha = str(sha256 or "").strip() or _read_key_file(SHA256_FILE)
    region = _verify_region(box_area)

    if not cid or not sha:
        # Without the keys there is nothing to verify against. Report UNKNOWN
        # rather than falling back to a weaker check that could pass wrongly.
        record_step(run_id, 6, "confirm_paste", "UNKNOWN",
                    "chat_id/sha256 not supplied — cannot verify the 2 keys")
        log("step6: UNKNOWN — no chat_id/sha256 to verify against")
        return {"ok": False, "status": "UNKNOWN", "answer": ""}

    # PROVE the env, then capture ONCE. Both questions are asked about this same
    # image, so focus cannot drift between them.
    if hwnd:
        rep = prove_env(hwnd)
        if not rep["ok"]:
            activate_window(hwnd)
            rep = prove_env(hwnd)
        if not rep["ok"]:
            record_step(run_id, 6, "confirm_paste", "UNKNOWN",
                        "window not foreground, refusing to judge: " + rep["detail"])
            return {"ok": False, "status": "UNKNOWN", "answer": ""}
    img = capture_crop(region, tag="confirm_paste")

    ans_id = _ask_image(img, "Does this image contain the text '%s'?" % cid)
    ans_sha = _ask_image(img, "Does this image contain the text '%s'?" % sha)
    if ans_id == "YES" and ans_sha == "YES":
        status = "PASS"
        detail = "7B-VL: chat_id + sha256 both visible in the chat box"
    elif ans_id == "NO" or ans_sha == "NO":
        status = "FAIL"
        detail = ("7B-VL: key not visible (chat_id=%s sha256=%s)"
                  % (ans_id, ans_sha))
    else:
        status = "UNKNOWN"
        detail = ("7B-VL inconclusive (chat_id=%s sha256=%s) — no judgement made"
                  % (ans_id, ans_sha))
    record_step(run_id, 6, "confirm_paste", status, detail, area=region)
    log(f"step6: {status} {detail} (region={region})")
    return {"ok": status == "PASS", "status": status,
            "answer": "%s/%s" % (ans_id, ans_sha)}


def inside(inner: tuple[int, int, int, int],
           outer: tuple[int, int, int, int]) -> bool:
    """True when `inner` is fully contained in `outer`.

    Used as verification LEVEL 1: the send button AREA must lie inside the
    measured chat-box AREA. A button outside the box means the measurement is
    wrong, so the click must be refused rather than attempted.
    """
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def find_send_button(box_area: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """MEASURE the blue send button INSIDE the chat box.

    WHY inside-only: the button lives in the container's bottom-right, and the
    container's own blue border runs along its edges. The search is therefore
    restricted to the interior (inset), or the border itself would be measured
    as the "button".

    WHY measured AFTER the paste: the button is HIDDEN while the box is empty
    (measured 2026-09-20: an empty box yielded 0 blue pixels), so measuring it
    before the paste would find nothing and a "button missing" result would be
    meaningless.

    A TIGHT blue threshold: a loose one also matches the button's anti-aliased
    halo plus other blue UI, producing a bbox as wide as the whole container
    (measured: b-r>40 -> x 400..1399, useless). b-r>80 isolates the disc.
    """
    import numpy as np
    import pyautogui

    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot().convert("RGB")
    a = np.array(shot, dtype=np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    blue = (b - r > 80) & (b > 180) & (g < b)

    x1, y1, x2, y2 = box_area
    inset = 14   # keep clear of the container's own blue border
    sx0, sy0 = max(0, x1 + inset), max(0, y1 + inset)
    sx1, sy1 = x2 - inset, y2 - inset
    if sx1 - sx0 < 10 or sy1 - sy0 < 10:
        log("find_send_button: container inset too small")
        return None
    sub = blue[sy0:sy1, sx0:sx1]
    ys, xs = np.where(sub)
    if len(ys) < 30:      # a disc, not a stray anti-aliased pixel
        log(f"find_send_button: only {len(ys)} blue px inside the box")
        return None
    btn = (int(xs.min()) + sx0, int(ys.min()) + sy0,
           int(xs.max()) + sx0, int(ys.max()) + sy0)
    log(f"find_send_button: ({btn[0]},{btn[1]})-({btn[2]},{btn[3]}) "
        f"from {len(ys)} px")
    return btn


def step7_send(run_id: str, box_area: tuple[int, int, int, int],
               hwnd: int | None = None) -> dict:
    """Send by CLICKING the blue button, verified at TWO levels.

    User spec 2026-09-20: the send is NOT a hotkey — it is a click on the blue
    arrow image, whose area must lie INSIDE the chat box.

    LEVEL 1 (geometry): the button AREA must be fully INSIDE the measured
      chat-box AREA. A button outside the box means the measurement is wrong,
      so the click is REFUSED — a click at an unproven coordinate is exactly the
      false success env_task_proof exists to stop.
    LEVEL 2 (read-back): after the click the box must be EMPTY, which the user
      confirmed is what a successful send produces.

    Both levels are required: geometry alone could click a correctly placed
    button the app ignored, and read-back alone could report success from a
    stray send. Together they cannot both be fooled.
    """
    import pyautogui

    pyautogui.FAILSAFE = False
    if hwnd:
        rep = prove_env(hwnd)
        if not rep["ok"]:
            activate_window(hwnd)
            rep = prove_env(hwnd)
        if not rep["ok"]:
            record_step(run_id, 7, "send", "UNKNOWN",
                        "window not foreground, refusing to send: " + rep["detail"])
            return {"ok": False, "status": "UNKNOWN"}

    empty_before, _ = box_is_empty(emptiness_area(box_area))
    if empty_before:
        record_step(run_id, 7, "send", "FAIL",
                    "box is empty — nothing to send (refusing to click)")
        return {"ok": False, "status": "FAIL"}

    btn = find_send_button(box_area)
    # ---- LEVEL 1: geometry ----
    if not btn:
        record_step(run_id, 7, "send", "UNKNOWN",
                    "send button not found inside the chat box")
        return {"ok": False, "status": "UNKNOWN"}
    if not inside(btn, box_area):
        record_step(run_id, 7, "send", "FAIL",
                    "send button %s is NOT inside the chat box %s — refusing to click"
                    % (btn, box_area))
        log(f"step7: FAIL L1 button {btn} outside box {box_area}")
        return {"ok": False, "status": "FAIL"}
    _save_area(SEND_TARGET, btn, "豆包 send button")
    cx, cy = (btn[0] + btn[2]) // 2, (btn[1] + btn[3]) // 2

    pyautogui.click(cx, cy)

    # ---- LEVEL 2: read-back ----
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: box_is_empty(emptiness_area(box_area))[0],
            timeout=6.0, poll=0.3,
            description="box empties after clicking the send button",
        )
        empty_after, mx = True, 0.0
    except cbw.ConditionTimeout:
        empty_after, mx = box_is_empty(emptiness_area(box_area))

    if empty_after:
        detail = ("L1 button %s inside box %s (OK) + L2 box emptied "
                  "-> sent (clicked %d,%d)" % (btn, box_area, cx, cy))
        record_step(run_id, 7, "send", "PASS", detail, area=btn,
                    input_type="icon")
        log(f"step7: PASS {detail}")
        return {"ok": True, "button": btn, "centre": (cx, cy)}

    status = "UNKNOWN" if mx <= TEXT_ROW_STD else "FAIL"
    record_step(run_id, 7, "send", status,
                "L1 button %s inside box (OK) but L2 box still has text "
                "(max row_std %.1f) after clicking (%d,%d)"
                % (btn, mx, cx, cy), area=btn, input_type="icon")
    log(f"step7: {status} clicked ({cx},{cy}) but box not empty")
    return {"ok": False, "status": status, "button": btn}


def step8_confirm_send(run_id: str, box_area: tuple[int, int, int, int],
                       hwnd: int | None = None) -> dict:
    """Confirm the send by MEASURING that the input box is empty.

    WHY a measurement and not the VL: "is this box empty?" is a question the
    7B-VL answered inconsistently (measured 2026-09-20: it reported text for a
    box that was visibly empty). Emptiness is a pixel property — text makes
    glyph rows with high horizontal variance — so it is measured directly.

    This matches the app's own contract, confirmed by the user: a SUCCESSFUL
    send returns the chat box to empty, so an empty box IS the success signal.
    """
    if hwnd:
        rep = prove_env(hwnd)
        if not rep["ok"]:
            activate_window(hwnd)
            rep = prove_env(hwnd)
        if not rep["ok"]:
            record_step(run_id, 8, "confirm_send", "UNKNOWN",
                        "window not foreground, refusing to judge: " + rep["detail"])
            return {"ok": False, "status": "UNKNOWN", "answer": ""}

    empty, mx = box_is_empty(emptiness_area(box_area))
    if empty:
        status = "PASS"
        detail = "input box measured EMPTY (max row_std %.1f) -> message sent" % mx
    else:
        status = "FAIL"
        detail = ("input box still holds text (max row_std %.1f >= %d) -> "
                  "not sent" % (mx, TEXT_ROW_STD))
    record_step(run_id, 8, "confirm_send", status, detail, area=box_area)
    log(f"step8: {status} {detail}")
    return {"ok": status == "PASS", "status": status, "answer": "%.1f" % mx}


# ---------------------------------------------------------------------------
# run-all
# ---------------------------------------------------------------------------

def run_all(chat_id: str = "", sha256: str = "") -> int:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    log(f"=== run {run_id} start ===")
    ensure_log_table()

    s1 = step1_find_window(run_id)
    if not s1.get("ok"):
        log(f"=== run {run_id} ABORT at step 1 ===")
        return 1

    s2 = step2_prove_env(run_id, s1["hwnd"])
    if not s2.get("ok"):
        log(f"=== run {run_id} ABORT at step 2 (env not proven) ===")
        return 1

    s3 = step3_find_chatbox(run_id, s1["rect"], s1["hwnd"])
    if not s3.get("ok"):
        log(f"=== run {run_id} ABORT at step 3 (box not measured) ===")
        return 2

    step4_click_chatbox(run_id, s3["centre"])
    step5_paste(run_id, chat_id, sha256)
    s6 = step6_confirm_paste(run_id, s3["area"], s1["hwnd"], chat_id, sha256)
    if not s6.get("ok"):
        # Do NOT send an unproven paste: sending the wrong content is worse than
        # not sending. The failure is already logged.
        log(f"=== run {run_id} ABORT at step 6 (paste not proven) ===")
        return 2 if s6.get("status") == "UNKNOWN" else 1

    step7_send(run_id, s3["area"], s1["hwnd"])
    s8 = step8_confirm_send(run_id, s3["area"], s1["hwnd"])
    log(f"=== run {run_id} done (step8={s8.get('status')}) ===")
    return 0 if s8.get("ok") else 2


def print_log(limit: int = 40) -> None:
    """Print the log table.

    Reconfigures stdout to UTF-8 first: the log legitimately contains Chinese
    (the 豆包 window title), and a cp950 console raises UnicodeEncodeError while
    printing — which would crash the reporter rather than show the data.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ensure_log_table()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM doubao_send_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    if not rows:
        print("(no log rows)")
        return
    print("%-20s %-4s %-14s %-8s %-7s %s"
          % ("run_id", "step", "name", "status", "type", "detail"))
    for r in reversed(rows):
        kind = r["input_type"] if "input_type" in r.keys() else "text"
        print("%-20s %-4s %-14s %-8s %-7s %s"
              % (r["run_id"], r["step_no"], r["step_name"], r["status"],
                 kind, (r["detail"] or "")[:60]))


def check() -> int:
    ok = True
    try:
        import pyautogui  # noqa: F401
        import numpy  # noqa: F401
        print("pyautogui + numpy: OK")
    except Exception as e:
        print(f"pyautogui/numpy MISSING: {e}")
        ok = False
    try:
        t = _template_text()
        print(f"template {TEMPLATE_KEY}: OK ({len(t)} chars)")
    except Exception as e:
        print(f"template {TEMPLATE_KEY} MISSING: {e}")
        ok = False
    try:
        ensure_log_table()
        print(f"log table: OK ({DB_PATH.name})")
    except Exception as e:
        print(f"log table FAIL: {e}")
        ok = False
    wins = find_doubao_windows()
    print(f"豆包 windows visible: {len(wins)}")
    for w in wins:
        print("  hwnd=%s maximized=%s fg=%s title=%r"
              % (w["hwnd"], w["maximized"], w["foreground"], w["title"]))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive the 豆包 desktop app (8 proven steps).")
    ap.add_argument("--detect", action="store_true", help="list candidate 豆包 windows")
    ap.add_argument("--find-window", action="store_true", help="step 1")
    ap.add_argument("--prove-env", action="store_true", help="step 2")
    ap.add_argument("--find-chatbox", action="store_true", help="step 3")
    ap.add_argument("--click-chatbox", action="store_true", help="step 4")
    ap.add_argument("--paste", action="store_true", help="step 5")
    ap.add_argument("--confirm-paste", action="store_true", help="step 6")
    ap.add_argument("--send", action="store_true", help="step 7")
    ap.add_argument("--confirm-send", action="store_true", help="step 8")
    ap.add_argument("--run-all", action="store_true", help="all 8 steps, logged")
    ap.add_argument("--log", action="store_true", help="print the log table")
    ap.add_argument("--check", action="store_true", help="deps + DB + template")
    # The two keys WE supply (user spec 2026-09-20). They are required to
    # GENERATE a verifiable prompt; without them the run can only paste the raw
    # template and the paste cannot be verified against real values.
    ap.add_argument("--chat-id", default="", help="the chat_id this prompt carries")
    ap.add_argument("--sha256", default="", help="the sha256 this prompt carries")
    ap.add_argument("--gen-prompt", action="store_true",
                    help="generate the identity prompt (needs --chat-id + --sha256) and print it")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the last generated prompt")
    args = ap.parse_args()

    if args.gen_prompt:
        try:
            text = generate_identity_prompt(
                chat_id=args.chat_id, sha256=args.sha256
            )
        except Exception as e:
            print(f"FAIL: {e}")
            return 1
        p = write_generated_prompt(text)
        print(text)
        print(f"\n[written to {p.name}]")
        return 0
    if args.show_prompt:
        if not GENERATED_PROMPT_FILE.exists():
            print("FAIL: no generated prompt yet (run --gen-prompt)")
            return 1
        print(GENERATED_PROMPT_FILE.read_text(encoding="utf-8"))
        return 0

    if args.check:
        return check()
    if args.log:
        print_log()
        return 0
    if args.detect:
        wins = find_doubao_windows()
        print(json.dumps(wins, ensure_ascii=False, indent=2))
        return 0 if wins else 1
    if args.run_all:
        return run_all(args.chat_id, args.sha256)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    ensure_log_table()

    if args.find_window:
        return 0 if step1_find_window(run_id).get("ok") else 1
    if args.prove_env:
        w = pick_doubao_window()
        if not w:
            print("FAIL: no 豆包 window")
            return 1
        return 0 if step2_prove_env(run_id, w["hwnd"]).get("ok") else 1
    if args.find_chatbox:
        rect = _load_area(WIN_TARGET)
        if not rect:
            print("FAIL: window area not measured yet (run --find-window)")
            return 1
        w = pick_doubao_window()
        hwnd = w["hwnd"] if w else None
        return 0 if step3_find_chatbox(run_id, rect, hwnd).get("ok") else 2
    if args.click_chatbox:
        area = _load_area(BOX_TARGET)
        if not area:
            print("FAIL: chat box area not measured yet (run --find-chatbox)")
            return 1
        return 0 if step4_click_chatbox(
            run_id, ((area[0] + area[2]) // 2, (area[1] + area[3]) // 2)
        ).get("ok") else 1
    if args.paste:
        return 0 if step5_paste(run_id, args.chat_id, args.sha256).get("ok") else 1
    if args.confirm_paste:
        area = _load_area(BOX_TARGET)
        if not area:
            print("FAIL: chat box area not measured yet")
            return 1
        w = pick_doubao_window()
        hwnd = w["hwnd"] if w else None
        return 0 if step6_confirm_paste(
            run_id, area, hwnd, args.chat_id, args.sha256
        ).get("ok") else 2
    if args.send:
        area = _load_area(BOX_TARGET)
        if not area:
            print("FAIL: chat box area not measured yet")
            return 1
        w = pick_doubao_window()
        hwnd = w["hwnd"] if w else None
        return 0 if step7_send(run_id, area, hwnd).get("ok") else 1
    if args.confirm_send:
        area = _load_area(BOX_TARGET)
        if not area:
            print("FAIL: chat box area not measured yet")
            return 1
        w = pick_doubao_window()
        hwnd = w["hwnd"] if w else None
        return 0 if step8_confirm_send(run_id, area, hwnd).get("ok") else 2

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
