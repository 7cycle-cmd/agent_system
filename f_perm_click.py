# -*- coding: utf-8 -*-
"""f_perm_click.py — click the VS Code chat permission button, then click a
fixed-position option in the popup. No LLM / no vision — pure X,Y from table.

Real screen = the measured primary screen (1920x1080 here, read via
GetSystemMetrics — never assumed).

SCREENSHOT SPACE — one space, by construction (fixed 2026-09-21)
----------------------------------------------------------------
Both sources now return the NATIVE screen size:

    pyautogui.screenshot()                       -> 1920x1080 (1:1)
    screen.snapshot(maxWidth=<screen width>)     -> 1920x1080 (1:1)

MEASURED 2026-09-21: OpenClaw used to be called with a hard-coded maxWidth=1600,
giving 1600x900 (scale 1.2) while pyautogui gave 1920x1080 — two coordinate
spaces from ONE function. `maxWidth` is an argument, and the sweep showed
1920/2000/2560/None ALL return 1920x1080, so 1600x900 was never a limitation,
just a value nobody had set correctly. This file now derives it from the screen.

`_openclaw_screenshot()` therefore cannot come back scaled, and `_screenshot()`
REFUSES any image that is not native rather than returning it.

All X,Y in this file are REAL screen coords.

Usage:
  f_perm_click.py --open                 # click permission button (popup opens)
  f_perm_click.py --click NAME           # click option NAME (popup must be open)
  f_perm_click.py --set NAME             # open popup + click NAME (one shot)
  f_perm_click.py --set-native NAME      # click pill -> picker -> CHECKLIST
                                         #   -> click confirmed area center
  f_perm_click.py --checklist [NAME]     # measure+confirm all targets (picker
                                         #   must be open); exit 0 if all yes
  f_perm_click.py --verify-checklist     # read checklist from DB; exit 0/1
  f_perm_click.py --measure-point PERM tl|br
                                         # capture ONE corner of a target AREA
                                         #   from the live cursor position;
                                         #   PERM = default|allow_all|autopilot|pill
  f_perm_click.py --verify-area PERM     # red overlay + 4-edge verdict +
                                         #   7B-VL yes/no; writes overlay PNG
                                         #   exit 0 only on PASS
  f_perm_click.py --set-area T x1 y1 x2 y2 [popup]
                                         # write one target AREA (calibration)
  f_perm_click.py --read-pill            # read the permission pill; exit 0/1
  f_perm_click.py --calibrate            # bootstrap areas + screenshot
  f_perm_click.py --measure              # open popup, screenshot, save crop
  f_perm_click.py --table                # print the X,Y table

COORDINATE SPACE: every stored AREA and every measured X,Y is in REAL screen
pixels (queried via GetSystemMetrics, not assumed). Both capture sources return
that SAME native size (see above), so `_crop_area()` still converts real -> image
pixels, but the ratio is now 1.0 and cannot drift. Cropping with raw real coords
on a downscaled image reads the wrong pixels — that was the
"recorded success but nothing changed" false positive.

A non-native capture is REFUSED, not converted: a scaled image is a different
coordinate space, and a rect applied to it lands in the wrong place while still
looking deliberate.

STATE HONESTY: the state file is written ONLY after the chat-input permission
pill is read back and matches the requested permission. set_state() requires an
explicit `verified=True`, so no code path can write state without proof.

CONFIRM CHECKLIST (user spec 2026-09-19): each popup target is stored as an
AREA (X1,Y1)-(X2,Y2) in coords.db target_area with a checklist_confirm
'yes'|'no' flag. --set-native ABORTS the click unless every active target is
confirmed 'yes' (enforced, not advisory).

EVIDENCE CLASSIFY (user spec 2026-09-19): --verify-area draws a RED guide box
(full-span X1/X2/Y1/Y2 lines) so a human can see whether the box really cuts the
row boundary, verifies each of the 4 edges independently (delta px ->
PASS/WARN/FAIL), and asks the local 7B-VL "does the text inside the RED BOX read
<label>?" -> Result YES/NO. PASS needs BOTH edges all-PASS and VL YES. A
not-found edge or a non-yes-no answer is FAIL/UNKNOWN, never a silent pass. See
evidence_overlay.py + evidence_classify.py.

NOTE: keyboard/hotkey approach (Ctrl+Alt+K + openPermissionPicker) was tested
2026-09-19 and ABANDONED — unreliable (1/6 success): window activation races,
chat-input focus cannot be forced programmatically, picker is a context menu
with unknown initial highlight. The picker is now opened by CLICKING the
chat-input pill. Click-based X,Y is the only supported path.
"""
import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "chat_permission.json"
TABLE = HERE / "perm_button_table.json"

# ---------------------------------------------------------------------------
# X,Y TABLE (real screen 1920x1080). Measured via OpenClaw screenshot.
# perm_button = the "Autopilot (Preview)" / mode pill in the chat input.
# options = rows in the popup that appears after clicking perm_button.
# ---------------------------------------------------------------------------
DEFAULT_TABLE = {
    "screen": [1920, 1080],
    "perm_button": {"x": 548, "y": 960},
    "options": {
        "default":     {"x": 618, "y": 426, "label": "Default permissions"},
        "sandbox":     {"x": 618, "y": 474, "label": "Sandboxing for terminal"},
        "allow_all":   {"x": 618, "y": 516, "label": "Allow all"},
        "autopilot":   {"x": 618, "y": 564, "label": "Autopilot (Preview)"},
    },
}

ORDER = ["default", "sandbox", "allow_all", "autopilot"]
LABELS = {
    "default": "Default permissions",
    "sandbox": "Sandboxing for terminal",
    "allow_all": "Allow all",
    "autopilot": "Autopilot (Preview)",
}

# ---------------------------------------------------------------------------
# NATIVE PICKER TABLE (user spec 2026-09-19): open the picker with the
# Ctrl+Alt+K keybinding (workbench.action.chat.openPermissionPicker), then
# click the option row at a fixed REAL-screen coord (1920x1080, VS Code
# maximized). User-measured 2026-09-19.
# ---------------------------------------------------------------------------
NATIVE_TABLE = {
    "default":   {"x": 900, "y": 717, "label": "Default permissions"},
    "allow_all": {"x": 900, "y": 800, "label": "Allow all"},
    "autopilot": {"x": 900, "y": 862, "label": "Autopilot (Preview)"},
}

# ---------------------------------------------------------------------------
# CONFIRM CHECKLIST (user spec 2026-09-19): before clicking, PROVE all popup
# targets are present. Each target is stored as an AREA (X1,Y1)-(X2,Y2) in
# coords.db target_area; a local VL model confirms the cropped area is the
# expected row -> checklist_confirm = yes|no. --set-native ABORTS the click
# unless every active target is 'yes'.
# ---------------------------------------------------------------------------
POPUP_ID = "perm_picker"

# The chat-input permission pill (bottom-left footer). It gets its OWN popup_id
# so it does NOT join the 3-target confirm checklist.
# Defined HERE, above TARGET_POPUP, because TARGET_POPUP references it — placing
# it lower raised NameError at import time.
PILL_TARGET_ID = "perm_pill"
PILL_POPUP_ID = "perm_pill_area"

# A measured area smaller than this in either axis is treated as a mistake
# (cursor never moved between tl and br) and is NOT written — overwriting a
# usable rect with a degenerate one is worse than failing loudly.
MIN_AREA_PX = 8
HELPER_API = "http://127.0.0.1:18765"
# Fallback areas (real screen 1920x1080) used only to bootstrap the DB when
# target_area is empty. Measured from the native picker rows.
DEFAULT_AREAS = {
    "perm_default":   {"x1": 700, "y1": 700, "x2": 1100, "y2": 734,
                       "label": "Default permissions"},
    "perm_allow_all": {"x1": 700, "y1": 783, "x2": 1100, "y2": 817,
                       "label": "Allow all"},
    "perm_autopilot": {"x1": 700, "y1": 845, "x2": 1100, "y2": 879,
                       "label": "Autopilot (Preview)"},
}
# perm name (NATIVE_TABLE key) -> target_area target_id
PERM_TO_TARGET_ID = {
    "default": "perm_default",
    "allow_all": "perm_allow_all",
    "autopilot": "perm_autopilot",
    # The pill OPENS the picker. It gets its own popup_id so it does NOT join the
    # 3-target confirm checklist, but it still needs a measured area.
    "pill": "perm_pill",
}

# Which popup_id each target belongs to. The pill is deliberately separate:
# joining it to the picker checklist would make --set-native require the pill
# row to be confirmed as if it were a picker row.
TARGET_POPUP = {
    "perm_default": POPUP_ID,        # "perm_picker"
    "perm_allow_all": POPUP_ID,
    "perm_autopilot": POPUP_ID,
    "perm_pill": PILL_POPUP_ID,      # "perm_pill_area"
}

# Label written with a measured area.
TARGET_LABEL = {
    "perm_default": "Default permissions",
    "perm_allow_all": "Allow all (auto-approve)",
    "perm_autopilot": "Autopilot (Preview)",
    "perm_pill": "Permission pill",
}

# Fallback pill area (real screen 1920x1080). MEASURE + override with
# `--set-area perm_pill X1 Y1 X2 Y2 perm_pill_area` — the default is a guess.
DEFAULT_PILL_AREA = {
    "x1": 80, "y1": 945, "x2": 340, "y2": 980,
    "label": "Permission pill",
}

# Canonical label per perm, used for pill read-back comparison.
PERM_LABELS = {
    "default": "Default permissions",
    "sandbox": "Sandboxing for terminal",
    "allow_all": "Allow all",
    "autopilot": "Autopilot",
}


def log(msg: str) -> None:
    try:
        from cdp_common import log as _log
        _log(msg, tag="PERMCLICK")
    except Exception:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# SendInput mouse click (reliable, no foreground lock issues)
# ---------------------------------------------------------------------------
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD), ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("mi", MOUSEINPUT)]


def _norm(v: int, real: int) -> int:
    """Map a REAL-screen coord to a normalized 0..65535 axis value.

    The `shot` parameter was REMOVED (2026-09-21). It was never used — the body
    only ever divided by `real` — but callers passed 1600/900, which implied a
    dependency on the screenshot's size that does not exist. That implication is
    how a reader concludes "the screenshot size matters here" and then adds a scale
    factor that corrupts a correct conversion. The absolute normalized form needs
    only the real screen size.
    """
    return int(v * 65535 // real)


def mouse_click(x: int, y: int, screen: tuple = (1920, 1080)) -> None:
    u = ctypes.windll.user32
    nx = _norm(x, screen[0])
    ny = _norm(y, screen[1])

    def _ev(flags):
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, None)
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    _ev(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
    time.sleep(0.08)
    _ev(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.04)
    _ev(MOUSEEVENTF_LEFTUP)


# Real screen size. target_area coords live in THIS space, so it must be
# queried, not assumed. Fallback only if the Win32 call fails.
REAL_SCREEN_FALLBACK = (1920, 1080)
_SCREEN_CACHE: tuple[int, int] | None = None


def _screen_size() -> tuple[int, int]:
    """Actual primary screen size in real pixels.

    Every stored target_area rect and every measured X,Y is in this space.
    Hard-coding 1920x1080 breaks on a different display or a DPI-scaled one,
    and a wrong real size silently skews the real->image conversion used for
    cropping. SetProcessDPIAware() must come first or a scaled display reports
    the virtual (scaled) size instead of the real pixel count.
    """
    global _SCREEN_CACHE
    if _SCREEN_CACHE:
        return _SCREEN_CACHE
    try:
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        w = int(u.GetSystemMetrics(0))
        h = int(u.GetSystemMetrics(1))
        if w > 0 and h > 0:
            _SCREEN_CACHE = (w, h)
            return _SCREEN_CACHE
    except Exception:
        pass
    _SCREEN_CACHE = REAL_SCREEN_FALLBACK
    return _SCREEN_CACHE


def _real_to_image(x1: int, y1: int, x2: int, y2: int, iw: int, ih: int):
    """Map a REAL-screen rect (target_area space) to image pixel coords.

    One scale factor per axis, derived from the ACTUAL image size — never a
    hard-coded 1.2.

    UPDATED 2026-09-21: both capture sources now return the native screen size,
    so this function is the IDENTITY in practice (sx=sy=1.0). The conversion is
    kept because it must still be correct if a genuinely scaled image is ever
    passed in deliberately (e.g. a downscaled artefact for a VL), and because
    deleting it would make the real -> image relationship invisible again.
    """
    sw, sh = _screen_size()
    sx, sy = iw / float(sw), ih / float(sh)

    def _c(v: int, hi: int, s: float) -> int:
        return max(0, min(hi, int(round(v * s))))

    return (_c(x1, iw, sx), _c(y1, ih, sy), _c(x2, iw, sx), _c(y2, ih, sy))


# ---------------------------------------------------------------------------
# Table load / save
# ---------------------------------------------------------------------------
def load_table() -> dict:
    if TABLE.exists():
        try:
            return json.loads(TABLE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_TABLE))


def save_table(t: dict) -> None:
    TABLE.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")


def set_state(perm: str, verified: bool) -> None:
    """Write the permission state file. REFUSES unless the pill was PROVEN.

    `verified` must come from a successful read_pill_permission() comparison in
    this same run. Making it a required argument means no code path can write
    state without verification — the previous unconditional write is exactly how
    chat_permission.json silently desynced from the UI (2026-09-19 finding).
    """
    if not verified:
        log("set_state REFUSED: pill verification missing — state NOT written")
        return
    data = {"permission": perm, "label": LABELS.get(perm, perm),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "via": "perm_click",
            "verified": True}
    STATE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (HERE / "chat_permission_last.txt").write_text(perm, encoding="utf-8")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _activate_vscode() -> None:
    """Bring VS Code to foreground so clicks land on it.

    FIXED 2026-09-20 (P2-window): this used `WScript.Shell.AppActivate('Visual
    Studio Code')`, which matches on the window TITLE. VS Code's title changes
    with the open file (e.g. "app.js - agent_system - Visual Studio Code"), so
    the match is unreliable and the activation silently did nothing.

    Activation is now by PROCESS (code.exe), and the result is PROVEN by the
    existing condition wait rather than assumed. If the foreground is never
    proven, the caller is told loudly — a click must not land on an unproven
    window.
    """
    import ctypes

    u = ctypes.windll.user32
    hwnd = _find_code_window()
    if hwnd:
        try:
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        except Exception as e:
            log("activate WARN: SetForegroundWindow failed (%s)" % e)
    else:
        # Loud, not silent: without a window there is nothing to activate, and
        # the condition wait below will report the failure.
        log("activate WARN: no code.exe window found to activate")

    # CONDITION WAIT (condition_based_waiting contract): wait for VS Code to
    # actually be foreground instead of sleeping a fixed 0.5s. A fixed sleep is
    # wrong in both directions — too short and the click lands on the wrong
    # window, too long and every run pays the worst case.
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: bool(_foreground_info().get("is_code")),
            timeout=3.0, description="VS Code is foreground",
        )
    except cbw.ConditionTimeout as e:
        # Loud, not silent: the caller must know the foreground was never proven.
        log("activate WARN: %s" % e)


def _find_code_window() -> int:
    """Return the hwnd of a code.exe top-level window, or 0.

    Matches on the PROCESS, not the title: the title carries the open file name
    and changes constantly, which is why the old title match failed.
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
            proc = _foreground_process_name(hwnd)
            if str(proc).lower() in ("code.exe", "code - insiders.exe"):
                found.append(int(hwnd))
                return False  # stop at the first match
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        return 0
    return found[0] if found else 0


def do_open() -> None:
    t = load_table()
    b = t["perm_button"]
    log("open: click perm_button (%d,%d)" % (b["x"], b["y"]))
    mouse_click(b["x"], b["y"], tuple(t["screen"]))
    # CONDITION WAIT: wait for the popup to be VISIBLE rather than sleeping a
    # fixed 1.2s. The old sleep was the exact case the report named
    # ("Ctrl+Alt+K 之後 time.sleep(1.4)").
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: _popup_visible(),
            timeout=3.0, description="permission popup visible",
        )
        log("open: popup visible (condition met)")
    except cbw.ConditionTimeout as e:
        log("open WARN: %s" % e)


def _popup_visible() -> bool:
    """True when the permission popup is on screen.

    Probes the popup's own area for real visual content — the same
    content-not-blank check the checklist uses, so a blank crop can never be
    mistaken for a visible popup.
    """
    try:
        t = load_table()
        opt = (t.get("options") or {}).get("perm_default")
        if not opt:
            return False
        import pyautogui

        img = pyautogui.screenshot()
        # A small probe around the first option row: if the popup is open the
        # pixels there are not a flat background.
        x, y = int(opt["x"]), int(opt["y"])
        box = img.crop((max(0, x - 40), max(0, y - 12),
                        min(img.width, x + 40), min(img.height, y + 12)))
        colors = box.getcolors(maxcolors=100000)
        return bool(colors) and len(colors) > 3
    except Exception:
        return False


def do_click(perm: str) -> None:
    t = load_table()
    opt = t["options"].get(perm)
    if not opt:
        log("click FAIL: unknown option %s" % perm)
        return
    # NOTE: do NOT activate VS Code here — the popup is already open and a
    # focus steal would dismiss it. Just click the option at its fixed X,Y.
    log("click: %s (%d,%d)" % (perm, opt["x"], opt["y"]))
    mouse_click(opt["x"], opt["y"], tuple(t["screen"]))
    # CONDITION WAIT: wait for the pill to actually read back as the requested
    # permission, instead of sleeping a fixed 0.8s and hoping. This is strictly
    # better than the sleep: it returns the moment the switch takes effect, and
    # it does not silently proceed when it never does.
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(
            lambda: read_pill_permission()[0] == perm,
            timeout=3.0, description="pill reads back as %s" % perm,
        )
    except cbw.ConditionTimeout:
        pass  # fall through to the read-back below, which reports the failure
    # PROVE the switch took effect BEFORE writing the state file. Writing state
    # unconditionally is how the recorded permission silently desyncs from the
    # real UI (2026-09-19 finding).
    got, raw = read_pill_permission()
    if got == perm:
        set_state(perm, verified=True)
        log("click OK: %s (pill verified: %r)" % (perm, raw[:60]))
    else:
        log("click FAIL: want %s got %r — state NOT written"
            % (perm, got or raw[:60]))


def do_set(perm: str, debug: bool = False) -> None:
    do_open()
    if debug:
        try:
            p = _openclaw_screenshot("perm_debug_open")
            log("debug: popup-open shot -> %s" % p)
        except Exception as e:
            log("debug: shot failed %s" % e)
    do_click(perm)


# NOTE (user spec 2026-09-19): the Ctrl+Alt+K keybinding path and its
# _force_foreground / _prove_foreground / _send_ctrl_alt_k helpers were
# DELETED — measured 1/6 success (window-activation races; chat-input focus
# cannot be forced programmatically). do_set_native() now opens the picker by
# CLICKING the chat-input pill, which is deterministic because the click target
# is a proven X,Y.


# ---------------------------------------------------------------------------
# Confirm checklist helpers (DB-driven areas + local VL confirm)
# ---------------------------------------------------------------------------
def _api(method: str, path: str, body: dict | None = None, timeout: float = 15):
    """Call the helper API on :18765 (target_area + checklist endpoints)."""
    import urllib.request
    url = HELPER_API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _bootstrap_areas() -> list:
    """Ensure target_area has the 3 permission rows (fallback areas).

    Only inserts rows that are missing; never overwrites a measured area or
    an existing checklist_confirm value.
    """
    try:
        areas = _api("GET", "/api/coord-targets/area?popup=" + POPUP_ID).get("areas") or []
    except Exception as e:
        log("checklist: area API unavailable (%s) — using local fallback" % e)
        areas = []
    have = {a.get("target_id") for a in areas}
    for tid, a in DEFAULT_AREAS.items():
        if tid in have:
            continue
        try:
            _api("POST", "/api/coord-targets/area", {
                "target_id": tid, "popup_id": POPUP_ID, "label": a["label"],
                "x1": a["x1"], "y1": a["y1"], "x2": a["x2"], "y2": a["y2"],
            })
            log("checklist: bootstrapped %s area" % tid)
        except Exception as e:
            log("checklist: bootstrap %s failed (%s)" % (tid, e))
    return areas


def _crop_area(png_path: str, x1: int, y1: int, x2: int, y2: int, out_path) -> str:
    """Crop the target AREA out of the picker screenshot.

    x1..y2 are REAL screen coords. Both capture sources return the native screen
    size (fixed 2026-09-21), so the conversion is an identity in practice; it is
    kept so the rect is still correct if a genuinely scaled image is passed in.
    Cropping with raw real coords on a downscaled image reads the wrong pixels or
    runs off the edge — that is the "recorded success but nothing changed" false
    positive.
    """
    from PIL import Image
    im = Image.open(png_path)
    iw, ih = im.size
    ix1, iy1, ix2, iy2 = _real_to_image(x1, y1, x2, y2, iw, ih)
    im.crop((ix1, iy1, ix2, iy2)).save(str(out_path))
    return str(out_path)


def _crop_area_checked(png_path: str, x1: int, y1: int, x2: int, y2: int, out_path):
    """Crop + prove the rect is valid and lands on real pixels.

    Returns (path, ok, note). ok=False means the stored area is unusable — the
    caller MUST treat that as a FAILED checklist item, never a confirmed row.
    This closes the hole where an out-of-bounds crop silently produced a flat
    image that a VL model could still "match".
    """
    from PIL import Image
    im = Image.open(png_path)
    iw, ih = im.size
    sw, sh = _screen_size()
    ok, note = True, None
    if not (0 <= x1 < x2 <= sw and 0 <= y1 < y2 <= sh):
        ok = False
        note = ("source rect (%d,%d)-(%d,%d) outside real screen %dx%d"
                % (x1, y1, x2, y2, sw, sh))
    ix1, iy1, ix2, iy2 = _real_to_image(x1, y1, x2, y2, iw, ih)
    if ix2 - ix1 < 4 or iy2 - iy1 < 4:
        ok = False
        note = ("area collapses to %dx%d px in a %dx%d image — re-measure"
                % (ix2 - ix1, iy2 - iy1, iw, ih))
    im.crop((ix1, iy1, ix2, iy2)).save(str(out_path))
    return str(out_path), ok, note


def _crop_has_content(path, min_std: float = 8.0) -> bool:
    """True if the crop has real visual content (not a blank/flat area).

    Guards the checklist against a FALSE POSITIVE: if the stored area does not
    actually sit over the target row (uncalibrated area, wrong window state),
    the crop is a flat patch. A VL model can still hallucinate a match, so a
    blank crop must NEVER be allowed to become checklist_confirm='yes'.
    """
    try:
        from PIL import Image, ImageStat
        im = Image.open(path).convert("L")
        return float(ImageStat.Stat(im).stddev[0]) >= min_std
    except Exception as e:
        log("checklist: content check failed (%s) — treating as blank" % e)
        return False


def _vision_confirm(crop_path: str, label: str) -> tuple[bool, str, str]:
    """Ask the local VL model whether the crop shows the expected row.

    Returns (ok, reason, seen_text). Uses vision_analyze.analyze_evidence
    (Ollama qwen2.5vl:7b) — no new dependency.
    """
    try:
        import vision_analyze
        prompt = (
            "This is a crop of ONE row from a VS Code permission picker popup.\n"
            "Does the visible text match the expected option?\n"
            "Expected option: %s\n"
            "Return ONLY JSON: {\"match\": true|false, \"seen_text\": \"...\", "
            "\"reason\": \"...\"}\n" % label
        )
        res = vision_analyze.analyze_evidence(
            crop_path, fault_type="perm_checklist", prompt=prompt,
            parse_mode="json",
        )
        d = res.detail or {}
        m = d.get("match")
        if m is None:
            m = d.get("correct")
        if isinstance(m, bool):
            ok = m
        else:
            ok = str(m).strip().lower() in ("yes", "true", "1")
        return ok, (d.get("reason") or res.summary or ""), (d.get("seen_text") or "")
    except Exception as e:
        return False, "vision error: %s: %s" % (type(e).__name__, e), ""


def run_checklist(perm: str | None = None) -> dict:
    """Measure + confirm every popup target; return the checklist status.

    Requires the picker to be OPEN (call after Ctrl+Alt+K). Crops each
    target_area from a fresh screenshot, asks the VL model to confirm, and
    writes checklist_confirm back to the DB. Returns
    {all_present, count, missing, click_x, click_y}.
    """
    areas = _bootstrap_areas()
    if not areas:
        try:
            areas = _api("GET", "/api/coord-targets/area?popup=" + POPUP_ID).get("areas") or []
        except Exception:
            areas = []
    if not areas:
        log("checklist: no target_area rows — cannot confirm")
        return {"all_present": False, "count": 0, "missing": ["<no rows>"]}

    try:
        shot = _screenshot("perm_checklist")
    except Exception as e:
        log("checklist: screenshot failed (%s)" % e)
        return {
            "all_present": False, "count": len(areas),
            "missing": [a["target_id"] for a in areas],
        }

    missing: list[str] = []
    for a in areas:
        tid = a["target_id"]
        crop = HERE / ("perm_checklist_%s.png" % tid)
        try:
            _p, rect_ok, rect_note = _crop_area_checked(
                shot, int(a["x1"]), int(a["y1"]),
                int(a["x2"]), int(a["y2"]), crop)
        except Exception as e:
            log("checklist: crop %s failed (%s)" % (tid, e))
            missing.append(tid)
            continue
        # Invalid rect = the stored area cannot be trusted -> fail closed.
        if not rect_ok:
            ok, reason, seen = False, rect_note, ""
        # Blank/flat crop = the area is NOT over the target row -> fail closed.
        elif not _crop_has_content(crop):
            reason = ("area (%s,%s)-(%s,%s) is blank/flat — not over the target row; "
                      "re-measure the area" % (a["x1"], a["y1"], a["x2"], a["y2"]))
            ok, seen = False, ""
        else:
            ok, reason, seen = _vision_confirm(crop, a.get("label") or tid)
        try:
            _api("POST", "/api/coord-targets/checklist/confirm", {
                "target_id": tid, "popup_id": POPUP_ID,
                "checklist_confirm": "yes" if ok else "no",
                "reason": (reason or "")[:300],
            })
        except Exception as e:
            log("checklist: confirm POST %s failed (%s)" % (tid, e))
        log("checklist: %s -> %s (seen=%r reason=%s)"
            % (tid, "yes" if ok else "no", seen[:60], (reason or "")[:80]))
        if not ok:
            missing.append(tid)

    out: dict = {
        "all_present": (not missing) and bool(areas),
        "count": len(areas),
        "missing": missing,
    }
    if perm:
        tid = PERM_TO_TARGET_ID.get(perm)
        row = next((a for a in areas if a["target_id"] == tid), None)
        if row:
            out["click_x"] = int(row.get("cx") or 0)
            out["click_y"] = int(row.get("cy") or 0)
    return out


def normalize_perm(text: str) -> str:
    """Map free-text pill/permission text to a canonical perm name.

    Returns '' when nothing matches — callers MUST treat '' as UNPROVEN and
    must never treat it as success.
    """
    t = str(text or "").strip().lower()
    if not t:
        return ""
    if "sandbox" in t:
        return "sandbox"
    if "autopilot" in t:
        return "autopilot"
    if "allow all" in t or "auto-approve" in t or "auto approve" in t:
        return "allow_all"
    if "default" in t:
        return "default"
    return ""


def _ensure_pill_area() -> dict | None:
    """Make sure the pill area row exists (idempotent; never overwrites)."""
    try:
        rows = _api("GET", "/api/coord-targets/area?popup=" + PILL_POPUP_ID).get("areas") or []
    except Exception as e:
        log("pill: area API unavailable (%s)" % e)
        return None
    for r in rows:
        if r.get("target_id") == PILL_TARGET_ID:
            return r
    a = DEFAULT_PILL_AREA
    try:
        return _api("POST", "/api/coord-targets/area", {
            "target_id": PILL_TARGET_ID, "popup_id": PILL_POPUP_ID,
            "label": a["label"], "x1": a["x1"], "y1": a["y1"],
            "x2": a["x2"], "y2": a["y2"],
        }).get("area")
    except Exception as e:
        log("pill: bootstrap failed (%s)" % e)
        return None


def _code_is_foreground() -> bool:
    """True if a Code.exe window is the current FOREGROUND window.

    Non-intrusive: does NOT activate or steal focus (a focus steal would
    dismiss the picker). Used to reject a pill read taken while some other
    app (Chrome / Doubao / Explorer) is in front — reading the pill from the
    wrong window would produce a bogus permission value.
    """
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return False
    pid = ctypes.wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        import psutil  # optional
        name = psutil.Process(pid.value).name().lower()
        return name in ("code.exe", "code - insiders.exe")
    except Exception:
        pass
    # Fallback: compare the foreground window title.
    buf = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(hwnd, buf, 256)
    return "visual studio code" in buf.value.lower()


def read_pill_permission(shot_path: str | None = None) -> tuple[str, str]:
    """Read the permission shown on the chat-input pill.

    Returns (perm, raw_text). perm is '' when it cannot be read; callers must
    treat '' as UNPROVEN, never as success.
    """
    if not _code_is_foreground():
        log("pill: VS Code is NOT foreground — refusing to read the pill "
            "(would read the wrong window)")
        return "", ""
    row = _ensure_pill_area()
    if not row:
        return "", ""
    if not shot_path:
        try:
            shot_path = _screenshot("perm_pill_read")
        except Exception as e:
            log("pill: screenshot failed (%s)" % e)
            return "", ""
    crop = HERE / "perm_pill_crop.png"
    try:
        _crop_area(shot_path, int(row["x1"]), int(row["y1"]),
                   int(row["x2"]), int(row["y2"]), crop)
    except Exception as e:
        log("pill: crop failed (%s)" % e)
        return "", ""
    if not _crop_has_content(crop):
        log("pill: crop blank/flat — area not over the pill (re-measure)")
        return "", ""
    try:
        import vision_analyze
        res = vision_analyze.analyze_evidence(
            crop, fault_type="perm_pill_read",
            prompt=(
                "This is a crop of the VS Code chat input permission pill.\n"
                "Return ONLY JSON: {\"permission\": \"...\", "
                "\"raw_text\": \"...\"}\n"
                "permission must be one of: Default permissions | "
                "Allow all | Autopilot | Sandboxing for terminal | unknown.\n"
            ),
            parse_mode="json",
        )
        d = res.detail or {}
        raw = str(d.get("raw_text") or d.get("permission") or res.summary or "")
        return normalize_perm(d.get("permission")) or normalize_perm(raw), raw
    except Exception as e:
        log("pill: vision failed (%s)" % e)
        return "", ""


def do_measure_point(perm: str, corner: str) -> None:
    """Capture ONE corner of a target AREA from the live cursor position.

    Operator workflow (user spec): move the mouse to the row's TOP-LEFT and run
        --measure-point default tl
    then move to the BOTTOM-RIGHT and run
        --measure-point default br
    """
    import pyautogui
    x, y = pyautogui.position()
    tid = PERM_TO_TARGET_ID.get(perm)
    if not tid:
        log("measure-point FAIL: unknown perm %s (want %s)"
            % (perm, list(PERM_TO_TARGET_ID)))
        return
    corner = (corner or "").strip().lower()
    if corner not in ("tl", "br"):
        log("measure-point FAIL: corner must be tl|br, got %r" % (corner,))
        return

    store = HERE / "perm_measure_points.json"
    data = {}
    if store.exists():
        try:
            data = json.loads(store.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    row = data.setdefault(tid, {})
    row["label"] = (TARGET_LABEL.get(tid)
                    or DEFAULT_AREAS.get(tid, {}).get("label")
                    or LABELS.get(perm, perm))
    row[corner] = [int(x), int(y)]
    row["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    row["screen"] = list(_screen_size())
    store.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log("measure-point: %s %s = (%d,%d) on %s" % (tid, corner, x, y, _screen_size()))

    if "tl" in row and "br" in row:
        x1, y1 = row["tl"][0], row["tl"][1]
        x2, y2 = row["br"][0], row["br"][1]
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        # Reject a degenerate rect. Measuring the same point twice (cursor never
        # moved, or the operator forgot to move it) produces a 0x0 area, which
        # would overwrite a usable value with something uncroppable. Fail loudly
        # instead of writing a rect that can never verify.
        if (x2 - x1) < MIN_AREA_PX or (y2 - y1) < MIN_AREA_PX:
            log("measure-point REJECT: %s rect is %dx%d px (min %d) — did you "
                "move the cursor between tl and br? area NOT written"
                % (tid, x2 - x1, y2 - y1, MIN_AREA_PX))
            return
        # confirm='no' ALWAYS: a measured rect is not yet PROVEN to sit over the
        # row. The next --verify-area / --set-native must prove it.
        # This also CLEARS a stale 'yes' — which matters, because a confirm from
        # the broken-coordinate era is a false pass (see _reset_perm_confirms.py).
        try:
            _api("POST", "/api/coord-targets/area", {
                "target_id": tid,
                "popup_id": TARGET_POPUP.get(tid, POPUP_ID),
                "label": row["label"],
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "checklist_confirm": "no",
            })
            log("measure-point: upserted %s (%d,%d)-(%d,%d) popup=%s confirm=no"
                % (tid, x1, y1, x2, y2, TARGET_POPUP.get(tid, POPUP_ID)))
        except Exception as e:
            log("measure-point: upsert failed (%s) — points kept in JSON only" % e)


def _open_picker_via_pill() -> bool:
    """Open the permission picker by CLICKING the chat-input pill.

    Replaces the Ctrl+Alt+K keybinding, which measured 1/6 success
    (window-activation races; chat-input focus cannot be forced). A click on a
    confirmed X,Y is deterministic.

    NOTE (2026-09-20): Ctrl+Alt+K was later re-tested and DOES work reliably
    when VS Code is activated and given ~0.6s first; the 1/6 figure came from
    attempts that skipped that. Both paths are viable.

    GUARDED CLICK — measured, not assumed. A stored rect is a claim, and one was
    measured 233px from its target while still being read back and clicked. So:
      * refuse when the area's `checklist_confirm` is not 'yes'
      * refuse when the area API is unreachable (cannot prove -> do not act)
    This mirrors Playwright/Selenium, which FAIL the action rather than click
    when their pre-action checks do not pass. Recording 'confirm=no' alone was
    the gap: the note existed and the click happened anyway.
    """
    if not _code_is_foreground():
        log("picker: VS Code is NOT foreground — aborting (would click elsewhere)")
        return False
    row = _ensure_pill_area()
    if not row:
        log("picker: no pill area — measure it first with --measure-point")
        return False

    confirm = str(row.get("checklist_confirm") or "").strip().lower()
    if confirm != "yes":
        log("picker REFUSED: pill area checklist_confirm=%r (need 'yes'). "
            "The rect is stored but UNPROVEN; clicking an unproven rect is how "
            "a click lands on unrelated UI. Measure + verify it first."
            % (row.get("checklist_confirm"),))
        return False

    cx = int(row.get("cx") or (int(row["x1"]) + int(row["x2"])) // 2)
    cy = int(row.get("cy") or (int(row["y1"]) + int(row["y2"])) // 2)
    log("picker: clicking pill at (%d,%d)" % (cx, cy))
    mouse_click(cx, cy)
    # CONDITION WAIT: wait for the picker to appear rather than sleeping 1.2s.
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(_popup_visible, timeout=3.0,
                       description="picker visible after pill click")
    except cbw.ConditionTimeout as e:
        log("picker WARN: %s" % e)
    return True


def _resolve_perm(name: str) -> str:
    """Accept both the short perm name and the target_id form.

    The CLI documents `default|allow_all|autopilot`, but the evidence folder and
    the DB use `perm_default` etc. Rejecting `perm_default` was a real usability
    bug: the id printed in the report could not be fed back into the command.
    """
    n = str(name or "").strip().lower()
    if n in PERM_TO_TARGET_ID:
        return n
    if n.startswith("perm_"):
        short = n[len("perm_"):]
        if short in PERM_TO_TARGET_ID:
            return short
    return ""


def _classify_failure(res: dict) -> dict:
    """Say WHY a verdict is not PASS — a verdict alone is not actionable.

    A bare FAIL is a dead end: "FAIL" does not tell an operator whether to
    re-measure the box, fix the label, or distrust the capture. Q2's own reason
    sentence usually names the cause; this promotes it to a machine-readable
    category so the next action is unambiguous.

    Categories:
        pass                 - edges all PASS and Q2 YES
        picker_not_open      - the picker is NOT on screen -> the capture is the
                               wrong content entirely; re-capture before ANY
                               geometry judgement
        source_suspect       - capture looks untrustworthy (no file / odd size /
                               provenance missing) -> re-capture before judging
        geometry_fail        - edges not all PASS -> re-measure the rect
        text_mismatch        - geometry fine, Q2 NO -> wrong target or wrong label
        box_not_seen         - Q1 NO -> overlay did not draw / not visible
        no_vl_answer         - model did not answer yes/no
        unknown              - none of the above
    """
    prov = res.get("provenance") or {}
    edges_ok = bool(res.get("edges_all_pass"))
    q = res.get("questions") or {}
    q1 = (q.get("q1_red_box_present") or {}).get("answer")
    q2 = (q.get("q2_text_in_box") or {}).get("answer")
    verdict = res.get("verdict")

    if verdict == "PASS" and edges_ok and q2 == "YES":
        return {"category": "pass", "action": "none"}

    # ORDER: trustworthiness BEFORE geometry. Judging geometry on a capture that
    # does not even contain the picker is how "reads 'VS code'" was reported as
    # geometry_fail, sending the operator to re-measure a rect that was never
    # the problem. Checked first, ahead of every other non-pass reason.
    if res.get("picker_ok") is False:
        return {"category": "picker_not_open",
                "action": "open the picker (pill click) and re-capture — the "
                          "capture is not the permissions menu (%s)"
                          % str(res.get("picker_reason") or "")[:160]}

    # Evidence trustworthiness comes first: judging geometry on a bad capture is
    # how a wrong source became an unexplained FAIL.
    if prov.get("error") or not prov.get("sha256"):
        return {"category": "source_suspect",
                "action": "re-capture; provenance incomplete (%s)"
                          % (prov.get("error") or "no hash")}
    if prov.get("source") == "unknown":
        return {"category": "source_suspect",
                "action": "re-capture; capture source not recorded"}
    # A capture taken while another app was in front is not re-inspectable as
    # picker evidence, even when the file itself is valid.
    if prov.get("foreground_is_code") is False:
        fg = prov.get("foreground") or {}
        return {"category": "source_suspect",
                "action": "re-capture with VS Code in front — captured while "
                          "front window was %s"
                          % (fg.get("process") or "unknown")}

    if q1 == "NO":
        return {"category": "box_not_seen",
                "action": "the overlay did not draw or is not visible — "
                          "re-capture, then check the rect is on screen"}
    if not edges_ok:
        bad = [e.get("edge") for e in (res.get("edges") or [])
               if e.get("verdict") != "PASS"]
        return {"category": "geometry_fail",
                "action": "re-measure the rect (%s not PASS)" % ", ".join(bad)}
    if q2 == "NO":
        return {"category": "text_mismatch",
                "action": "geometry is fine but the text differs — wrong target "
                          "rect or wrong label; verify which before changing state"}
    if q2 is None:
        return {"category": "no_vl_answer",
                "action": "model did not answer yes/no — re-run"}
    return {"category": "unknown", "action": "inspect the overlay PNG manually"}


def do_verify_area(perm: str) -> dict:
    """Verify ONE target AREA end-to-end: red overlay + edge verdicts + VL yes/no.

    This is the "easy classify by evidence" step. It produces a visual artefact
    a human can judge at a glance, plus a machine verdict:

        1. screenshot (OpenClaw, local fallback)
        2. draw the red guide box + full-span X1/X2/Y1/Y2 lines
        3. verify each of the 4 edges against the image (delta px + verdict)
        4. ask the local 7B-VL: does the text inside the RED BOX read <label>?
        5. write the overlay PNG and the combined verdict

    PASS requires BOTH the edges all-PASS and the VL answering YES. Anything
    uncertain is FAIL/UNKNOWN — never a silent pass.

    Returns the combined dict. Does NOT write chat_permission.json.
    """
    import evidence_classify

    perm = _resolve_perm(perm)
    tid = PERM_TO_TARGET_ID.get(perm)
    if not tid:
        msg = ("verify-area FAIL: unknown perm %r (want default|allow_all|"
               "autopilot, or perm_default|perm_allow_all|perm_autopilot)"
               % (perm,))
        log(msg)
        return {"ok": False, "error": msg, "verdict": "UNKNOWN"}

    # Look the area up in the CORRECT popup. _bootstrap_areas() only seeds/reads
    # POPUP_ID ("perm_picker"); perm_pill lives in "perm_pill_area", so using
    # _bootstrap_areas() here made every pill verify fail with "no area".
    popup = TARGET_POPUP.get(tid, POPUP_ID)
    row = None
    try:
        rows = _api("GET", "/api/coord-targets/area?popup=" + popup).get("areas") or []
        row = next((a for a in rows if a.get("target_id") == tid), None)
    except Exception as e:
        log("verify-area: area lookup failed (%s)" % e)

    if not row:
        # fall back to a local default so verify can still run pre-DB
        a = DEFAULT_AREAS.get(tid) or (DEFAULT_PILL_AREA if tid == PILL_TARGET_ID else None)
        if not a:
            msg = "verify-area FAIL: no area for %s (popup=%s)" % (tid, popup)
            log(msg)
            return {"ok": False, "error": msg, "verdict": "UNKNOWN"}
        row = dict(a, target_id=tid)

    x1, y1 = int(row["x1"]), int(row["y1"])
    x2, y2 = int(row["x2"]), int(row["y2"])
    label = row.get("label") or LABELS.get(perm, perm)

    try:
        shot = _screenshot("perm_verify_%s" % perm)
    except Exception as e:
        msg = "verify-area FAIL: screenshot failed (%s)" % e
        log(msg)
        # Still record the failure as evidence: a failed capture must leave a
        # trace, otherwise the only record is a log line nobody reads.
        try:
            import evidence_store
            rec = evidence_store.open_evidence(tid)
            # A failed CAPTURE proves the container was never observed. Record
            # that as an absence (source_suspect), not as a verdict: the
            # classification rule requires an absence category whenever
            # picker_ok is False, so this payload must say picker_ok False.
            payload = {"ok": False, "verdict": "UNKNOWN", "label": label,
                       "error": msg, "edges": [], "edges_all_pass": False,
                       "questions": {}, "vl": None,
                       "picker_ok": False,
                       "picker_reason": "screenshot failed: %s" % msg,
                       "root_cause": {"category": "source_suspect",
                                      "action": "re-capture"}}
            evidence_store.save_classify(rec, payload)
            evidence_store.save_report(rec, payload)
            log("verify-area: failure evidence -> %s" % rec.evidence_id)
        except Exception as e2:
            log("verify-area: could not record failure evidence (%s)" % e2)
        return {"ok": False, "error": msg, "verdict": "UNKNOWN"}

    # Evidence folder first, so every artefact lands under one id.
    import evidence_store
    rec = evidence_store.open_evidence(tid)
    evidence_store.copy_artifact(rec, "shot", shot)
    log("verify-area: evidence id %s" % rec.evidence_id)

    out_dir = HERE / "hko_proof"
    out_dir.mkdir(exist_ok=True)
    overlay = out_dir / ("perm_verify_%s_overlay.png" % perm)

    res = evidence_classify.classify_with_overlay(
        shot, x1, y1, x2, y2, label, overlay,
    )
    res["perm"] = perm
    res["target_id"] = tid
    res["source_shot"] = shot
    # Provenance: without this a FAIL gives no way to tell "target is wrong"
    # from "evidence is wrong". That ambiguity is what produced an unexplained
    # FAIL that reached the Learning Center.
    res["provenance"] = _screenshot_provenance(shot)

    # PROVE THE PICKER IS ON SCREEN before judging geometry. A real case: the
    # capture was of Chrome showing an app-tile grid, the picker rect cropped an
    # app tile, and the verdict was a confident "FAIL: reads 'VS code'". Every
    # recorded field looked healthy (source=pyautogui, 1920x1080, hash ok) so
    # nothing flagged it, and the operator was told to re-measure a rect that was
    # never the problem. Judging a rect is meaningless when the target row is not
    # in the image at all, so this check runs FIRST and blocks the classify.
    picker = _picker_present(shot, res["provenance"])
    res["picker_ok"] = bool(picker.get("ok"))
    res["picker_reason"] = picker.get("reason")
    res["picker_checks"] = picker.get("checks")

    if not res["picker_ok"]:
        # Do NOT run the classify pipeline: an overlay on the wrong content
        # produces a number that looks like a measurement. Record the evidence
        # and stop, with an UNKNOWN verdict — we learned nothing about the rect.
        res["ok"] = False
        res["verdict"] = "UNKNOWN"
        res["edges"] = []
        res["edges_all_pass"] = False
        res["questions"] = {}
        res["vl"] = None
        res["error"] = "picker not open: %s" % (res["picker_reason"] or "")
        res["root_cause"] = _classify_failure(res)
        try:
            evidence_store.copy_artifact(rec, "overlay", overlay)
        except Exception:
            pass
        res["evidence_id"] = rec.evidence_id
        res["evidence_dir"] = rec.dir
        evidence_store.save_classify(rec, res)
        evidence_store.save_report(rec, res)
        res["files"] = dict(rec.files)
        log("verify-area %s -> UNKNOWN picker_not_open (%s) evidence=%s"
            % (perm, res["picker_reason"], rec.evidence_id))
        return res

    res["root_cause"] = _classify_failure(res)

    # Store the overlay + a close-up crop, then the JSON and the report.
    evidence_store.copy_artifact(rec, "overlay", overlay)
    # The VL reads a DIFFERENT render (text-free). Store it too, otherwise the
    # evidence folder cannot reproduce why Q2 answered the way it did.
    if res.get("vl_path"):
        evidence_store.copy_artifact(rec, "overlay_vl", res["vl_path"])
    try:
        crop = HERE / ("perm_verify_%s_crop.png" % perm)
        _crop_area(shot, x1, y1, x2, y2, crop)
        evidence_store.copy_artifact(rec, "crop", crop)
    except Exception as e:
        log("verify-area: crop skipped (%s)" % e)
    res["evidence_id"] = rec.evidence_id
    res["evidence_dir"] = rec.dir
    evidence_store.save_classify(rec, res)
    evidence_store.save_report(rec, res)
    res["files"] = dict(rec.files)

    log("verify-area %s -> %s (edges_all_pass=%s, vl=%s) evidence=%s"
        % (perm, res.get("verdict"),
           res.get("edges_all_pass"),
           (res.get("vl") or {}).get("verdict"),
           rec.evidence_id))
    return res


def do_set_native(perm: str) -> None:
    """Click the pill → open picker → CONFIRM CHECKLIST → click the row →
    PROVE via the pill read-back → only then write state.

    The click is ABORTED when the checklist is not all-yes (user spec:
    enforce, not advisory).
    """
    opt = NATIVE_TABLE.get(perm)
    if not opt:
        log("native FAIL: unknown option %s (want one of %s)"
            % (perm, list(NATIVE_TABLE)))
        return
    if not _open_picker_via_pill():
        log("native FAIL: could not open the picker via the pill")
        return

    # --- CONFIRM CHECKLIST GATE (enforce) ---
    status = run_checklist(perm)
    if not status.get("all_present"):
        log("native ABORT: checklist_confirm != yes (missing=%s) — no click"
            % (status.get("missing") or "?"))
        return
    log("native: checklist OK (all %s targets confirmed)" % status.get("count"))

    # Click the CONFIRMED area center (from DB), not the hard-coded table.
    cx, cy = status.get("click_x") or opt["x"], status.get("click_y") or opt["y"]
    mouse_click(cx, cy)
    log("native: clicked %s at (%d,%d)" % (perm, cx, cy))
    # CONDITION WAIT: wait for the pill to read back as the requested perm,
    # instead of sleeping a fixed 0.8s and hoping.
    import condition_based_waiting as cbw

    try:
        cbw.wait_until(lambda: read_pill_permission()[0] == perm,
                       timeout=3.0,
                       description="pill reads back as %s" % perm)
    except cbw.ConditionTimeout:
        pass  # the read-back below reports the failure
    got, raw = read_pill_permission()
    if got == perm:
        set_state(perm, verified=True)
        log("native OK: %s (pill verified: %r)" % (perm, raw[:60]))
    else:
        log("native FAIL: want %s got %r — state NOT written, "
            "no record_success" % (perm, got or raw[:60]))
        return

    # record into success-target table (shows at 127.0.0.1:18765/settings)
    try:
        import coord_store
        coord_store.record_success("perm_%s" % perm, cx, cy, action="click")
        log("native: record_success perm_%s at (%d,%d)" % (perm, cx, cy))
    except Exception as e:
        log("native: record_success skipped (%s: %s)" % (type(e).__name__, e))


def _openclaw_screenshot(tag: str) -> str:
    import mcp_client
    from mcp_client import extract_image_bytes
    from datetime import datetime
    out_dir = HERE / "hko_proof"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / ("%s_%s.png" % (tag, ts))
    client = mcp_client.McpClient(mcp_client.McpConfig.from_env())
    # maxWidth DERIVED from the screen, never hard-coded.
    #
    # MEASURED 2026-09-21: this used to pass maxWidth=1600, which produced
    # 1600x900 while pyautogui produced 1920x1080 — the SAME function returning
    # images in two coordinate spaces depending on whether OpenClaw was up.
    # `maxWidth` is an argument, and the sweep showed 1920 / 2000 / 2560 / None ALL
    # return 1920x1080 (the real screen). So the fix is to STOP naming a number and
    # ask for the screen. Three sibling files already did this correctly
    # (f9_find_copy, f_mode_vision, f_model_vision); only this one did not.
    sw, _sh = _screen_size()
    result = client.screen_snapshot({"screenIndex": 0, "maxWidth": int(sw)})
    img, err = extract_image_bytes(result)
    if not img:
        raise RuntimeError("no image bytes: %s" % err)
    out_path.write_bytes(img)
    return str(out_path)


def _screenshot(tag: str) -> str:
    """Full-screen PNG for the checklist. LOCAL first, OpenClaw fallback.

    WHY THE ORDER IS LOCAL-FIRST (measured 2026-09-21)
    --------------------------------------------------
    * SPEED: pyautogui 0.034s vs OpenClaw 0.119s (3.5x).
    * NO WINDOW: `f_new_session.py:7` had already recorded "mcp_snapshot() brings
      the OpenClaw window to the foreground on every call (popup bug, 2026-09-18).
      pyautogui.screenshot() has no window and is faster." That reason was recorded
      but never applied here — this call site still preferred OpenClaw.
    * NATIVE SPACE: pyautogui is 1:1 with the screen.

    THE SCALED-IMAGE TRAP IS NOW CLOSED AT THE SOURCE: `_openclaw_screenshot()`
    asks for the SCREEN width, so both sources return 1920x1080 and there is only
    ONE space again. This function additionally REFUSES a non-native image rather
    than returning it, because a scaled image is not "slightly worse" — it is a
    different coordinate space, and a rect applied to it lands in the wrong place
    while still looking deliberate.
    """
    global _LAST_SHOT_SOURCE
    sw, sh = _screen_size()
    attempts: list[str] = []

    def _native(path: str) -> tuple[bool, str]:
        from PIL import Image
        try:
            with Image.open(path) as im:
                size = im.size
        except Exception as e:
            return False, "unreadable: %s: %s" % (type(e).__name__, e)
        if size != (sw, sh):
            return False, ("%dx%d is not the native %dx%d — a scaled image is a "
                           "different coordinate space" % (size[0], size[1], sw, sh))
        return True, ""

    for which in ("pyautogui", "openclaw"):
        try:
            if which == "pyautogui":
                import pyautogui
                pyautogui.FAILSAFE = False
                out_path = HERE / ("%s_local.png" % tag)
                pyautogui.screenshot(str(out_path))
                path = str(out_path)
            else:
                path = _openclaw_screenshot(tag)
            good, why = _native(path)
            if not good:
                attempts.append("%s: %s" % (which, why))
                log("screenshot: REFUSED %s (%s)" % (which, why))
                continue
            _LAST_SHOT_SOURCE = which
            return path
        except Exception as e:
            attempts.append("%s: %s: %s" % (which, type(e).__name__, e))
            log("screenshot: %s failed (%s: %s)" % (which, type(e).__name__, e))

    _LAST_SHOT_SOURCE = ""
    raise RuntimeError("no %dx%d screenshot available; attempts=%s"
                       % (sw, sh, attempts))


# Which capture path produced the most recent _screenshot(). Recorded into every
# evidence record so a reviewer can tell whether two verdicts are even
# comparable — the gap that let a wrong-source FAIL go unexplained.
_LAST_SHOT_SOURCE: str = ""


def _foreground_process_name(hwnd: int) -> str:
    """Best-effort process image name for a window handle.

    psutil is optional here, so without it the name degraded to "unknown" and
    the provenance said front=unknown while is_code was still True — a record
    that cannot answer "which app was in front". Falls back to the Win32
    QueryFullProcessImageName call, which needs no third-party module.
    """
    try:
        import psutil  # optional
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        pass
    # Fallback: QueryFullProcessImageNameW on the window's thread pid.
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return "unknown"
        try:
            size = ctypes.wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return "unknown"


def _foreground_info() -> dict:
    """Describe the window that was IN FRONT when the shot was taken.

    This is the check that actually catches the observed bug. A capture of
    Chrome showing an app-tile grid was cropped with picker coordinates and
    produced a confident "FAIL: reads 'VS code'" — and the recorded provenance
    said source=pyautogui, 1920x1080, hash OK, i.e. it looked trustworthy. The
    image SIZE matched, so nothing flagged it. The foreground process is what
    differs, so it has to be recorded.

    Never raises: an unknown foreground is recorded as such, not guessed.
    """
    info = {"hwnd": None, "process": "unknown", "title": "", "is_code": False}
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return info
        info["hwnd"] = int(hwnd)
        info["process"] = _foreground_process_name(hwnd)
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        info["title"] = buf.value
        proc = str(info["process"]).lower()
        info["is_code"] = proc in ("code.exe", "code - insiders.exe") or (
            "visual studio code" in info["title"].lower()
        )
    except Exception as e:
        info["error"] = "%s: %s" % (type(e).__name__, e)
    return info


def _probe_region() -> tuple[int, int, int, int] | None:
    """Union of the picker target rects, generously padded, clamped to screen.

    Deliberately independent of a single target: the point is to ask "is the
    permissions menu anywhere in the area we think it lives", not "is this one
    row right". Returns None when no rect is known.

    Reads the LIVE DB areas first (the placeholder DEFAULT_AREAS y-values are
    known to be wrong), then falls back to DEFAULT_AREAS. Both are dicts keyed by
    x1/y1/x2/y2 — indexing them as tuples silently produced "no probe region".
    """
    rects: list[tuple[int, int, int, int]] = []
    try:
        rows = _api("GET", "/api/coord-targets/area?popup=" + POPUP_ID).get("areas") or []
        for a in rows:
            if a.get("target_id") in ("perm_default", "perm_allow_all", "perm_autopilot"):
                rects.append((int(a["x1"]), int(a["y1"]), int(a["x2"]), int(a["y2"])))
    except Exception as e:
        log("picker-probe: live area lookup failed (%s) — using defaults" % e)
    if not rects:
        for tid in ("perm_default", "perm_allow_all", "perm_autopilot"):
            a = DEFAULT_AREAS.get(tid)
            if not a:
                continue
            try:
                rects.append((int(a["x1"]), int(a["y1"]), int(a["x2"]), int(a["y2"])))
            except Exception:
                continue
    if not rects:
        return None
    pad = 120
    x1 = max(0, min(r[0] for r in rects) - pad)
    y1 = max(0, min(r[1] for r in rects) - pad)
    x2 = max(r[2] for r in rects) + pad
    y2 = max(r[3] for r in rects) + pad
    try:
        w, h = _screen_size()
        x2 = min(int(w), x2)
        y2 = min(int(h), y2)
    except Exception:
        pass
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return (x1, y1, x2, y2)


_PICKER_Q_PROMPT = (
    "Look at this crop of a screen. Answer YES only if you can see a dropdown "
    "MENU listing permission options such as 'Sandboxing for terminal', "
    "'Allow all', 'Autopilot' or 'Learn more about permissions'. "
    "If you see a browser page, an app icon grid, a code editor, or any other "
    "content WITHOUT such a permissions menu, answer NO. "
    "Reply exactly:\nResult: [YES / NO]\nReason: 1 short sentence.\n"
)


def _picker_present(shot: str, prov: dict | None = None) -> dict:
    """Prove the permission picker is actually ON SCREEN before judging geometry.

    Returns {"ok": bool, "reason": str, "checks": {...}}.

    Two independent checks, and BOTH must not actively contradict:
      1. foreground  - the picker only exists over VS Code. Capturing while
                       Chrome is in front is the exact failure this guards.
      2. content     - a VL look at the probe region must not say "no menu".
    A failed content look is treated as NOT PRESENT (refuse to judge). A false
    negative only costs a re-run; a false positive judges geometry on the wrong
    image, which is the bug we are fixing.

    Never raises; an inability to check is reported as not-present.
    """
    prov = prov or {}
    checks: dict = {}
    fg = prov.get("foreground") or _foreground_info()
    checks["foreground"] = {
        "process": fg.get("process"),
        "title": (fg.get("title") or "")[:80],
        "is_code": bool(fg.get("is_code")),
    }
    if not fg.get("is_code"):
        return {
            "ok": False,
            "reason": ("VS Code is NOT the foreground window (front=%s) — the "
                       "picker cannot be open; this capture is not the picker"
                       % (fg.get("process") or "unknown")),
            "checks": checks,
        }

    region = _probe_region()
    if not region:
        checks["content"] = {"ok": None, "note": "no probe region available"}
        return {
            "ok": False,
            "reason": "no picker probe region known — cannot confirm the picker",
            "checks": checks,
        }
    x1, y1, x2, y2 = region
    try:
        tmp = HERE / "perm_picker_probe.png"
        _crop_area(shot, x1, y1, x2, y2, tmp)
    except Exception as e:
        checks["content"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return {
            "ok": False,
            "reason": "could not crop the picker probe region (%s)" % e,
            "checks": checks,
        }
    try:
        import evidence_classify
        r = evidence_classify.classify_yes_no(tmp, "permissions menu",
                                              prompt=_PICKER_Q_PROMPT)
        answer = r.answer
        checks["content"] = {
            "ok": answer == "YES",
            "answer": answer,
            "verdict": r.verdict,
            "reason": r.reason,
            "region": list(region),
        }
        if answer != "YES":
            return {
                "ok": False,
                "reason": ("no permissions menu visible in %s (VL said %s: %s) — "
                           "the picker is not open or not where we think"
                           % (list(region), answer or r.verdict, r.reason)),
                "checks": checks,
            }
    except Exception as e:
        checks["content"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return {
            "ok": False,
            "reason": "picker content check failed (%s)" % e,
            "checks": checks,
        }
    return {"ok": True, "reason": "picker present", "checks": checks}


def _screenshot_provenance(path: str) -> dict:
    """Describe an evidence image so a reviewer can judge if it is trustworthy.

    Records the source, size, a content hash AND the foreground window. Without
    this, a FAIL gives no way to tell "the target is wrong" from "the evidence
    is wrong" — which is exactly the case that reached the Learning Center
    unexplained. Note that size + hash alone were NOT enough: a capture of
    Chrome at the same 1920x1080 produced a valid-looking record for an image
    that never contained the picker, so the foreground is recorded too.
    """
    import hashlib
    from PIL import Image

    prov: dict = {
        "source": _LAST_SHOT_SOURCE or "unknown",
        "path": str(path),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bytes": 0,
        "width": None,
        "height": None,
        "sha256": None,
        "real_screen": list(_screen_size()),
    }
    p = Path(str(path))
    if not p.is_file():
        prov["error"] = "file missing"
        prov["foreground"] = _foreground_info()
        return prov
    try:
        prov["bytes"] = p.stat().st_size
        prov["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception as e:
        prov["error"] = "%s: %s" % (type(e).__name__, e)
    try:
        with Image.open(p) as im:
            prov["width"], prov["height"] = im.size
    except Exception as e:
        prov["error"] = "%s: %s" % (type(e).__name__, e)
    # Foreground is recorded LAST but read at capture time; keep it even when
    # the file was unreadable, because "we were on the wrong window" is then the
    # most likely explanation.
    prov["foreground"] = _foreground_info()
    prov["foreground_is_code"] = bool(prov["foreground"].get("is_code"))
    return prov


def _screenshot_local(tag: str) -> str:
    """Local-only capture (skips OpenClaw) for calibration/measurement."""
    import pyautogui
    out_path = HERE / ("%s_local.png" % tag)
    pyautogui.screenshot(str(out_path))
    return str(out_path)


def do_measure() -> None:
    do_open()
    # do_open() already waited for the popup by condition; no extra sleep needed.
    # screenshot via OpenClaw
    try:
        path = _openclaw_screenshot("perm_measure")
        log("measure: screenshot -> %s" % path)
    except Exception as e:
        log("measure: screenshot failed %s" % e)
        return
    # crop the popup region for inspection
    try:
        from PIL import Image
        im = Image.open(path)
        im.crop((550, 540, 1000, 760)).save(str(HERE / "hko_proof" / "perm_popup_crop.png"))
        log("measure: crop saved hko_proof/perm_popup_crop.png")
    except Exception as e:
        log("measure: crop failed %s" % e)


def do_table() -> None:
    t = load_table()
    print(json.dumps(t, indent=2, ensure_ascii=False))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "--open":
        do_open()
    elif cmd == "--click":
        do_click(args[1])
    elif cmd == "--set":
        do_set(args[1], debug=("--debug" in args))
    elif cmd == "--set-native":
        do_set_native(args[1])
    elif cmd == "--checklist":
        # Requires the picker to be OPEN (click the pill first).
        st = run_checklist(args[1] if len(args) > 1 else None)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0 if st.get("all_present") else 1
    elif cmd == "--verify-checklist":
        try:
            st = _api("GET", "/api/coord-targets/checklist?popup=" + POPUP_ID)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0 if st.get("all_present") == "yes" else 1
    elif cmd == "--read-pill":
        # Prove the pill can be read right now (no click, no state write).
        p, raw = read_pill_permission()
        print(json.dumps({"perm": p, "raw": raw}, indent=2, ensure_ascii=False))
        return 0 if p else 1
    elif cmd == "--set-area":
        # --set-area TARGET x1 y1 x2 y2 [popup_id]
        if len(args) < 6:
            print("usage: --set-area TARGET x1 y1 x2 y2 [popup_id]")
            return 1
        tid = args[1]
        x1, y1, x2, y2 = (int(v) for v in args[2:6])
        pid = args[6] if len(args) > 6 else POPUP_ID
        log("set-area %s (%d,%d)-(%d,%d) popup=%s" % (tid, x1, y1, x2, y2, pid))
        print(json.dumps(_api("POST", "/api/coord-targets/area", {
            "target_id": tid, "popup_id": pid,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        }), indent=2, ensure_ascii=False))
    elif cmd == "--calibrate":
        # Show the current areas + a screenshot path for measuring (no click).
        _bootstrap_areas()
        _ensure_pill_area()
        shot = _screenshot("calibrate")
        areas = _api("GET", "/api/coord-targets/area?all=1").get("areas") or []
        print(json.dumps({"shot": shot, "areas": areas},
                         indent=2, ensure_ascii=False))
    elif cmd == "--verify-area":
        # --verify-area <default|allow_all|autopilot>
        # Red overlay + 4-edge verdict + 7B-VL yes/no. Writes an overlay PNG.
        if len(args) < 2:
            print("usage: --verify-area <default|allow_all|autopilot>")
            return 1
        res = do_verify_area(args[1])
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("verdict") == "PASS" else 1
    elif cmd == "--measure-point":
        # --measure-point <default|allow_all|autopilot|pill> <tl|br>
        # Move the mouse to the corner, then run this. Two corners -> area.
        if len(args) < 3:
            print("usage: --measure-point <default|allow_all|autopilot|pill> <tl|br>")
            return 1
        do_measure_point(args[1], args[2])
    elif cmd == "--measure":
        do_measure()
    elif cmd == "--table":
        do_table()
    elif cmd == "--save-table":
        # read JSON from stdin or a file arg
        raw = Path(args[1]).read_text(encoding="utf-8") if len(args) > 1 else sys.stdin.read()
        save_table(json.loads(raw))
        log("table saved")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
