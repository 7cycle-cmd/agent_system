# -*- coding: utf-8 -*-
"""Resolve WHICH window belongs to a capture session's source.

WHY THIS MODULE EXISTS (measured 2026-09-20)
--------------------------------------------
`target_capture_ui._target_hwnd()` called `f_doubao_send.pick_doubao_window()`
unconditionally. It never looked at the session's `source_kind` / `source_ref`, so
a session whose source was `URL https://gemini.google.com/app` captured the DOUBAO
window anyway. Two consequences:

  * with 豆包 open, steps 1-4 screenshots showed the WRONG APP -> the LEVEL 1
    environment gate judged a different program (or worse, passed while proving
    the wrong thing);
  * with 豆包 closed, `_target_hwnd()` returned 0 -> a FULL-SCREEN capture, which
    happened to include Gemini. So the same session captured the right app or the
    wrong app depending on whether an unrelated program was running.

The design rule: the source is entered ONCE at session creation and REUSED for
every step, so the window must be derived from it — not from whichever window the
doubao helper happens to prefer.

Matching, in order, and each result states WHICH rule matched so a wrong match is
visible rather than plausible:

  APP  source_ref = an exe path (e.g. ...\\Doubao.exe)
       1. the process's own image path equals source_ref  (strongest, exact)
       2. a window whose title matches the exe's base name
  URL  source_ref = a URL (e.g. https://gemini.google.com/app)
       1. a BROWSER window (Edge / Chrome / Brave / Firefox) whose title carries
          the URL's host or its distinctive label
       2. any other visible window whose title carries that label
       NOTE: a browser tab title is not the URL. This matches the HOST or a label
       derived from it, which is what a tab title actually contains. When several
       browser windows match, `pick_window_for_source` returns the best one and
       the report lists the other candidates, so a human can see the ambiguity.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# Browser executables, by image base name (lower case). A URL source may only
# match one of these; matching a NOTEPAD window whose title contains "gemini"
# would be a confident wrong answer.
_BROWSER_EXES = {"msedge.exe", "chrome.exe", "brave.exe", "firefox.exe",
                 "opera.exe", "vivaldi.exe", "msedgewebview2.exe"}

# MEASURED FALSE POSITIVE 2026-09-20: `url_label("https://www.google.com")` is
# "google", and a Chrome window's title always ends with " - Google Chrome". So
# the host-label rule matched EVERY Chrome window for google.com — a confident
# wrong answer produced by a browser BRAND name, not a site name. The brand
# suffix is removed before matching.
#
# NOTE the brands are written WITHOUT zero-width characters; `_strip_brand()`
# normalizes those first (see below), so this list only needs the readable form.
_BROWSER_BRAND = (" - google chrome", " - microsoft edge", " - brave",
                  " - mozilla firefox", " - firefox", " - opera",
                  " - vivaldi", " - chromium", " - edge")

# MEASURED 2026-09-20: Edge's title is `... - Microsoft\u200b Edge` — a ZERO-WIDTH
# SPACE sits between "Microsoft" and "Edge". Matching brand strings literally
# therefore depended on an invisible character being copied correctly. Normalizing
# the separator characters makes the match about the words, not about bytes that
# cannot be seen.
_INVISIBLE = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00a0")


def _strip_brand(title: str) -> str:
    """A window title with the browser's own branding removed.

    Two things happen here, and both were measured problems:

    1. INVISIBLE SEPARATORS ARE NORMALIZED. Edge writes `Microsoft\\u200b Edge`
       (zero-width space). A brand list holding a plain space only matched by
       luck of which entry happened to be present.
    2. The BRANDING IS REMOVED because it is not evidence about WHICH SITE is
       displayed, so leaving it in lets a browser name act as a match (the
       measured google.com / "- Google Chrome" false positive).
    """
    t = str(title or "").lower()
    for ch in _INVISIBLE:
        t = t.replace(ch, " ")
    while "  " in t:
        t = t.replace("  ", " ")
    t = t.strip()
    for b in _BROWSER_BRAND:
        if t.endswith(b):
            t = t[: -len(b)]
    return t.strip()

# A label must be distinguishable from a brand/product word. These appear in
# window titles for reasons unrelated to the site being located.
_GENERIC_LABELS = {"google", "microsoft", "chrome", "edge", "brave", "firefox",
                   "opera", "vivaldi", "bing", "search", "mail", "drive",
                   "docs", "www"}

# Windows that carry a title but are not the app the user measured. Reused from
# the doubao finder's spirit: enumerating is better than guessing, but an
# un-filtered enumeration returns shells and tooltips.
_TITLE_DENY = ("program manager", "windows input experience", "default ime",
               "settings", "task manager")


def log(msg: str) -> None:
    print(msg, flush=True)


def _list_windows() -> list[dict[str, Any]]:
    """Every visible, titled top-level window with its process image path."""
    u = ctypes.windll.user32
    out: list[dict[str, Any]] = []
    fg = int(u.GetForegroundWindow() or 0)

    k32 = ctypes.windll.kernel32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            title = buf.value
            if not title.strip():
                return True
            r = wt.RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = _image_path(k32, int(pid.value))
            out.append({
                "hwnd": int(hwnd),
                "title": title[:200],
                "rect": (int(r.left), int(r.top), int(r.right), int(r.bottom)),
                "maximized": bool(u.IsZoomed(hwnd)),
                "foreground": int(hwnd) == fg,
                "pid": int(pid.value),
                "exe": exe,
                "exe_base": Path(exe).name.lower() if exe else "",
            })
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception as e:
        log("list_windows FAIL: %s: %s" % (type(e).__name__, e))
    return out


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _image_path(k32, pid: int) -> str:
    """Full exe path of a pid, or "" when it cannot be read.

    Empty is reported as empty: an unreadable process is UNKNOWN, not a match.
    """
    if pid <= 0:
        return ""
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wt.DWORD(4096)
        buf = ctypes.create_unicode_buffer(4096)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def _area(w: dict[str, Any]) -> int:
    x1, y1, x2, y2 = w["rect"]
    return max(0, x2 - x1) * max(0, y2 - y1)


def _rank(w: dict[str, Any]) -> tuple:
    return (w["foreground"], w["maximized"], _area(w))


def url_label(url: str) -> str:
    """The most distinctive token a window title would carry for this URL.

    MEASURED 2026-09-20: `https://gemini.google.com/app` renders a tab titled
    "Gemini", NOT the URL. So the label is the LEFT-most significant host label
    ("gemini"), and the full host is kept as a secondary candidate.
    """
    u = str(url or "").strip()
    for pre in ("https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    host = u.split("/")[0].split("?")[0].strip().lower()
    parts = [p for p in host.split(".") if p and p not in ("www", "com", "co",
                                                           "org", "net", "io",
                                                           "app", "ai")]
    return parts[0] if parts else host


def _read_awareness() -> str:
    """DPI awareness of this process, read RIGHT NOW.

    Callers must not compare this across an `import pyautogui` boundary — see
    pin_dpi_awareness().
    """
    try:
        a = ctypes.c_int()
        hr = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(a))
        if hr == 0:
            return {0: "UNAWARE", 1: "SYSTEM", 2: "PER_MONITOR"}.get(
                a.value, str(a.value))
    except Exception:
        pass
    try:
        return "SYSTEM" if ctypes.windll.user32.IsProcessDPIAware() else "UNAWARE"
    except Exception:
        return "unknown"


_DIAG_AWARENESS = _read_awareness()


def pin_dpi_awareness() -> dict[str, Any]:
    """FIX this process's DPI awareness so window rects have ONE meaning.

    MEASURED 2026-09-20 (and this is the environment factor that matters):
        start                   : UNAWARE
        after `import pyautogui`: SYSTEM

    `import pyautogui` calls SetProcessDPIAware() and permanently changes the
    process's DPI awareness. That changes what GetWindowRect RETURNS: a DPI-UNAWARE
    process gets VIRTUALIZED coordinates, an aware one gets PHYSICAL pixels. So a
    rect measured before the import and used after it (or the reverse) is in a
    different space than the code assumes — and at a non-100% display scale the two
    differ by the scale ratio. That is the same failure class as the measured 8px
    and 26px red-box offsets.

    At 100% scale the two spaces coincide, which is why this stayed invisible.

    So the space is PINNED here, once, instead of drifting with import order:
    every window rect this module returns is then in the same space as a
    `pyautogui.screenshot()` (physical pixels). Must be called BEFORE any rect is
    read; `resolve_window()` calls it, so callers cannot forget.

    Returns {was, now, pinned, method}. `pinned=False` means another part of the
    process fixed it first — reported, not assumed.
    """
    was = _read_awareness()
    out: dict[str, Any] = {"was": was, "now": was, "pinned": False,
                           "method": None, "diagnostic_preimport": _DIAG_AWARENESS}
    if was == "PER_MONITOR":
        out.update({"now": "PER_MONITOR", "pinned": True,
                    "method": "already PER_MONITOR"})
        return out
    u = ctypes.windll.user32
    shcore = getattr(ctypes.windll, "shcore", None)
    # 1. per-monitor v2 (the modern, unambiguous context)
    try:
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            out.update({"now": _read_awareness(), "pinned": True,
                        "method": "SetProcessDpiAwarenessContext(PER_MONITOR_V2)"})
            return out
    except Exception:
        pass
    # 2. per-monitor (Windows 8.1+)
    try:
        if shcore and shcore.SetProcessDpiAwareness(2) == 0:
            out.update({"now": _read_awareness(), "pinned": True,
                        "method": "SetProcessDpiAwareness(PER_MONITOR)"})
            return out
    except Exception:
        pass
    # 3. system aware (oldest fallback)
    try:
        if u.SetProcessDPIAware():
            out.update({"now": _read_awareness(), "pinned": True,
                        "method": "SetProcessDPIAware"})
            return out
    except Exception:
        pass
    # 4. nothing worked -> another part of the process already set it
    out.update({"now": _read_awareness(), "pinned": _read_awareness() != "UNAWARE",
                "method": "already set elsewhere"})
    return out


def env_report() -> dict[str, Any]:
    """The ENVIRONMENT FACTS a capture depends on. Measured, never assumed.

    WHY THIS EXISTS (user: "you miss the environment factor")
    --------------------------------------------------------
    A window rect is only meaningful inside a coordinate space, and that space is
    set by the environment:

      * DPI SCALE. This process is DPI_UNAWARE (measured 2026-09-20: value=0). With
        a scale of 100% that is harmless, but at 125% Windows VIRTUALIZES
        GetWindowRect while a screenshot is PHYSICAL pixels — so every mark would be
        off by the scale ratio. That is the same failure class as the measured 8px
        and 26px red-box offsets. Reported here so it can be refused, not suffered.
      * MULTI-MONITOR. A rect can be on a non-primary monitor or entirely off the
        primary one; "is the target on the same monitor as the capture" must be
        answerable.
      * MINIMIZED. Windows parks a minimized window at about (-32000, -32000), so
        its rect looks like a valid tiny window at the far corner. A capture of it
        is a capture of nothing.
      * SCREEN SIZE vs SCREENSHOT SIZE: a mismatch means the two are in different
        spaces and no rect can be trusted across them.
    """
    u = ctypes.windll.user32
    out: dict[str, Any] = {}
    # --- DPI awareness, read once and pinned ---
    # Pinning BEFORE anything else means the rects and the screenshot below are in
    # the same space by construction, rather than by luck about import order.
    pin = pin_dpi_awareness()
    out["dpi_pin"] = pin
    out["dpi_awareness"] = pin["now"]
    # --- scale ---
    try:
        hdc = u.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        u.ReleaseDC(0, hdc)
        out["logpixels_x"] = int(dpi)
        out["scale_pct"] = int(round(100.0 * dpi / 96.0))
    except Exception:
        out["logpixels_x"] = None
        out["scale_pct"] = None
    # --- screen vs screenshot ---
    sw, sh = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    out["screen"] = [sw, sh]
    out["virtual_screen"] = [int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))]
    out["virtual_origin"] = [int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77))]
    try:
        import pyautogui
        s = pyautogui.screenshot()
        out["screenshot"] = [s.size[0], s.size[1]]
        del s
    except Exception as e:
        out["screenshot"] = None
        out["screenshot_error"] = "%s: %s" % (type(e).__name__, e)
    # --- monitors ---
    mons: list[list[int]] = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                         ctypes.POINTER(wt.RECT), wt.LPARAM)

    def _mc(hmon, hdc, lprect, lparam):
        r = lprect.contents
        mons.append([int(r.left), int(r.top), int(r.right), int(r.bottom)])
        return True

    try:
        u.EnumDisplayMonitors(0, None, MONITORENUMPROC(_mc), 0)
    except Exception:
        pass
    out["monitors"] = mons
    # --- derived risks ---
    risks: list[str] = []
    if out["scale_pct"] and out["scale_pct"] != 100 and out["dpi_awareness"] == "UNAWARE":
        risks.append(
            "DPI_UNAWARE at %s%% scale: rects are VIRTUALIZED but screenshots are "
            "PHYSICAL, so a mark will be off by the ratio (%.2f)"
            % (out["scale_pct"], out["scale_pct"] / 100.0))
    if not pin.get("pinned"):
        risks.append("DPI awareness could NOT be pinned (%s): rects may be in a "
                     "different space than the screenshot" % pin.get("method"))
    if out["screenshot"] and out["screenshot"] != out["screen"]:
        risks.append("screenshot %s != screen %s — rects cannot be trusted across "
                     "the two spaces" % (out["screenshot"], out["screen"]))
    if len(mons) > 1:
        risks.append("%d monitors: a rect may be on a different monitor than the "
                     "capture" % len(mons))
    out["risks"] = risks
    out["ok"] = not risks
    return out


def _window_flags(hwnd: int) -> dict[str, bool]:
    """MINIMIZED / MAXIMIZED / parked-off-screen, measured per window.

    A minimized window's rect is about (-32000, -32000): a valid-looking tiny rect
    at the far corner. Capturing it yields a picture of nothing, so it must be
    EXCLUDED rather than treated as a candidate.
    """
    u = ctypes.windll.user32
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return {"minimized": bool(u.IsIconic(hwnd)),
            "zoom": bool(u.IsZoomed(hwnd)),
            "parked": int(r.left) <= -30000 or int(r.top) <= -30000}


def _on_primary(hwnd: int) -> bool:
    r = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    u = ctypes.windll.user32
    sw, sh = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    # overlaps the primary monitor at all?
    return not (r.right <= 0 or r.bottom <= 0 or r.left >= sw or r.top >= sh)


def cdp_tabs() -> dict[str, Any]:
    """Real tab URLs from Chrome DevTools Protocol, if a CDP Chrome is running.

    WHY CDP IS THE STRONGEST SIGNAL FOR A URL SOURCE
    ------------------------------------------------
    MEASURED 2026-09-20: a browser window's TITLE reports the ACTIVE tab only. The
    user had Gemini open in a background tab of a Chrome window titled
    "Target Capture — 6 STEP", so no title-based rule could ever find it — the
    approach was structurally unable to work, not merely mistuned. CDP reports
    every tab's actual URL, which is the data the URL source already holds.

    Returns {ok, tabs, error}. `ok=False` with a reason, never a silent empty list,
    so a caller can say WHY a URL source could not be resolved.
    """
    import urllib.request
    out: dict[str, Any] = {"ok": False, "tabs": [], "error": None}
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/list",
                                    timeout=3) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        out["error"] = ("CDP not available (%s: %s) — start it with "
                        "cdp_common.ensure_cdp()" % (type(e).__name__, e))
        return out
    for t in raw:
        if t.get("type") == "page":
            out["tabs"].append({"url": t.get("url") or "",
                                "title": t.get("title") or "",
                                "id": t.get("id") or ""})
    out["ok"] = True
    return out


def _host_match(target: str, url: str) -> bool:
    """Same registrable host, ignoring scheme/port/path."""
    def _norm(u: str) -> str:
        u = str(u or "").strip().lower()
        for pre in ("https://", "http://"):
            if u.startswith(pre):
                u = u[len(pre):]
                break
        return u.split("/")[0].split("?")[0].split(":")[0]
    a, b = _norm(target), _norm(url)
    return bool(a) and a == b


def resolve_window(source_kind: str, source_ref: str,
                   *, allow_fallback: bool = False) -> dict[str, Any]:
    """Find the window for a session source. Returns a report, never raises.

    {ok, hwnd, rule, window, candidates, detail}. `ok=False` means NO match was
    proven — the caller must NOT silently capture some other window.
    """
    out: dict[str, Any] = {"ok": False, "hwnd": 0, "rule": None, "window": None,
                           "candidates": [], "checked": 0, "detail": "",
                           "env": None, "excluded_minimized": 0, "cdp": None}
    kind = str(source_kind or "").strip().upper()
    ref = str(source_ref or "").strip()
    if not ref:
        out["detail"] = "source_ref empty — nothing to resolve against"
        return out

    # The environment is recorded WITH the resolution, so a rect can always be
    # read back in the context that produced it (scale, monitors, screen vs
    # screenshot). A resolution without its environment is not reproducible.
    out["env"] = env_report()

    wins = _list_windows()
    # Drop denylisted shells, zero-area windows, and MINIMIZED windows. A minimized
    # window is parked near (-32000, -32000) so its rect looks like a legitimate
    # tiny window in the far corner; capturing it produces a picture of nothing.
    # It is removed here rather than "handled later" so it can never be selected.
    cands = [w for w in wins
             if w["title"].strip().lower() not in _TITLE_DENY
             and (w["rect"][2] - w["rect"][0]) > 20
             and (w["rect"][3] - w["rect"][1]) > 20
             and not _window_flags(w["hwnd"])["minimized"]]
    out["checked"] = len(cands)
    out["excluded_minimized"] = sum(
        1 for w in wins if _window_flags(w["hwnd"])["minimized"])

    matches: list[tuple[str, dict]] = []

    if kind == "APP":
        want = Path(ref).name.lower()
        stem = want.rsplit(".", 1)[0] if want else ""
        # ORDER MATTERS, strongest first.
        # 1. exact image path. MEASURED 2026-09-20: the stored source_ref was
        #    `...\Doubao\Application\Doubao.exe` while the RUNNING image is
        #    `...\Doubao\Application\app\Doubao.exe` — an extra `app\` segment. So
        #    an exact-path match is the strongest signal but cannot be the ONLY
        #    one: an installer that adds a versioned subfolder would otherwise
        #    break every capture.
        a = [w for w in cands if w["exe"] and w["exe"].lower() == ref.lower()]
        if a:
            matches = [("exe path == source_ref", w) for w in a]
        else:
            # 2. same executable BASE NAME. The base name is what identifies the
            #    app; the directory can drift (subfolder, version, install root).
            b = [w for w in cands if want and w["exe_base"] == want]
            matches = [("exe base name == %s" % want, w) for w in b]
            if not matches:
                # 3. the app's name inside the title (an app whose title is its
                #    own name), excluding browsers, whose title is the tab.
                c = [w for w in cands
                     if stem and stem in w["title"].lower()
                     and w["exe_base"] not in _BROWSER_EXES]
                matches = [("title contains the app name %r" % stem, w) for w in c]
    elif kind == "URL":
        label = url_label(ref)
        host = ref.split("//")[-1].split("/")[0].lower()
        # A generic label ("google") matches a browser's BRAND, so it is not
        # allowed to select a window on its own. Only CDP or the full host may.
        label_usable = bool(label) and label not in _GENERIC_LABELS
        out["label"] = label
        out["label_usable"] = label_usable

        # --- RULE 1 (strongest): CDP reports the ACTUAL tab URLs ---
        # A browser window's title is the ACTIVE tab only, so a background tab is
        # unfindable by title. MEASURED 2026-09-20: Gemini was open in a background
        # tab of a window titled "Target Capture — 6 STEP"; no title rule could
        # ever have found it. CDP is the only instrument that sees it.
        cdp = cdp_tabs()
        out["cdp"] = {"ok": cdp["ok"], "tabs": len(cdp["tabs"]),
                      "error": cdp["error"]}
        hit_urls = [t["url"] for t in cdp["tabs"] if _host_match(ref, t["url"])]
        if hit_urls:
            out["cdp_match"] = hit_urls[0]
            # CDP proves the TAB exists; the WINDOW is still resolved separately,
            # because CDP does not report a window handle. Prefer a browser whose
            # title carries the matched tab's title; then the host label; then any
            # foreground browser.
            tab_titles = [_strip_brand(t["title"]) for t in cdp["tabs"]
                          if _host_match(ref, t["url"]) and t.get("title")]
            br = [w for w in cands if w["exe_base"] in _BROWSER_EXES]
            for t in tab_titles:
                m = [w for w in br if t and t in _strip_brand(w["title"])]
                if m:
                    matches = [("CDP tab URL matched, window title matched the tab",
                                w) for w in m]
                    break
            if not matches and label_usable:
                lb = [w for w in br
                      if label in _strip_brand(w["title"])]
                matches = [("CDP tab URL matched; window chosen by host label",
                            w) for w in lb]
            if not matches:
                fg = [w for w in br if w["foreground"]] or br
                matches = [("CDP tab URL matched; window chosen as the browser "
                            "(active tab is a different page)", w) for w in fg]

        # --- RULE 2: browser title contains the full host ---
        # Branding stripped, so "google" from "- Google Chrome" cannot match.
        if not matches:
            br = [w for w in cands if w["exe_base"] in _BROWSER_EXES]
            h = [w for w in br
                 if host and host in _strip_brand(w["title"])]
            matches += [("browser title contains the full host", w) for w in h]
            # --- RULE 3: browser title contains a DISTINCTIVE host label ---
            if label_usable:
                lb = [w for w in br
                      if label in _strip_brand(w["title"])]
                matches += [("browser title contains the host label %r" % label, w)
                            for w in lb]
            # --- RULE 4: any non-browser window titled with the label ---
            if label_usable:
                o = [w for w in cands
                     if label in _strip_brand(w["title"])
                     and w["exe_base"] not in _BROWSER_EXES]
                matches += [("any window title contains %r" % label, w)
                            for w in o if w not in [m[1] for m in matches]]
    else:
        out["detail"] = "source_kind must be APP or URL, got %r" % source_kind
        return out

    out["candidates"] = [{"hwnd": m[1]["hwnd"], "rule": m[0],
                          "title": m[1]["title"], "exe": m[1]["exe_base"],
                          "rect": m[1]["rect"], "foreground": m[1]["foreground"],
                          "maximized": m[1]["maximized"]}
                         for m in sorted(matches, key=lambda m: _rank(m[1]),
                                         reverse=True)]
    if not matches and allow_fallback:
        # AN EXPLICIT OPT-IN, and REPORTED when taken.
        # The default is allow_fallback=False: a no-match must be a FAILURE, not a
        # silent capture of an unrelated window. MEASURED 2026-09-20: the original
        # code captured 豆包 for a session whose source was
        # `URL https://gemini.google.com/app` and reported ok=True, so the LEVEL 1
        # environment gate then judged the WRONG PROGRAM — or passed while proving
        # the wrong thing. A caller that genuinely wants "whatever is there" must
        # ask for it, and the result says `fallback: True` so a gate can refuse it.
        try:
            import f_doubao_send as fd
            w = fd.pick_doubao_window()
            if w:
                out.update({"ok": True, "hwnd": int(w["hwnd"]),
                            "rule": "FALLBACK: source matched nothing; used the "
                                    "doubao window (NOT proven to be the source)",
                            "fallback": True,
                            "window": {"hwnd": int(w["hwnd"]),
                                       "title": w.get("title"),
                                       "rect": w.get("rect")}})
                out["detail"] = ("no window matched %s %r — fell back to 豆包 "
                                 "hwnd=%s" % (kind, ref, w["hwnd"]))
                return out
        except Exception as e:
            log("fallback pick failed: %s: %s" % (type(e).__name__, e))
        out["detail"] = ("no window matched %s %r among %d titled windows"
                         % (kind, ref, len(cands)))
        return out

    if not matches:
        # Refuse, and say exactly what was searched and what was seen. An empty
        # window handle the caller can check beats a plausible wrong one it cannot.
        out["candidates_seen"] = [
            {"hwnd": w["hwnd"], "exe": w["exe_base"], "title": w["title"][:90],
             "rect": w["rect"]}
            for w in sorted(cands, key=_rank, reverse=True)[:12]
        ]
        if kind == "URL" and not (out.get("cdp") or {}).get("ok"):
            out["detail"] = (
                "no window matched URL %r: a browser window title reports only the "
                "ACTIVE tab, so a background tab cannot be found by title, and CDP "
                "is not running (%s). Start it with cdp_common.ensure_cdp() to "
                "resolve a URL source reliably."
                % (ref, (out.get("cdp") or {}).get("error")))
        else:
            out["detail"] = ("no window matched %s %r among %d titled windows"
                             % (kind, ref, len(cands)))
        return out

    best_rule, best = sorted(matches, key=lambda m: _rank(m[1]),
                             reverse=True)[0]
    out.update({
        "ok": True,
        "hwnd": int(best["hwnd"]),
        "rule": best_rule,
        "fallback": False,
        "window": {"hwnd": int(best["hwnd"]), "title": best["title"],
                   "rect": best["rect"], "exe": best["exe"],
                   "maximized": best["maximized"],
                   "foreground": best["foreground"]},
        "detail": ("%s -> hwnd=%s %r (%d candidate(s))"
                   % (best_rule, best["hwnd"], best["title"][:60], len(matches))),
    })
    return out


if __name__ == "__main__":
    # The console here is cp950, so a window title containing non-Big5 characters
    # (e.g. a Gemini tab titled with Chinese text) raises UnicodeEncodeError and
    # the CLI dies AFTER doing correct work. Encode permissively.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    kind = sys.argv[1] if len(sys.argv) > 1 else "URL"
    ref = sys.argv[2] if len(sys.argv) > 2 else "https://gemini.google.com/app"
    r = resolve_window(kind, ref)
    env = r.get("env") or {}
    print("ok=%s hwnd=%s fallback=%s" % (r["ok"], r["hwnd"], r.get("fallback")))
    print("rule:", r["rule"])
    print("detail:", r["detail"])
    print("checked %d titled windows (%d minimized excluded)"
          % (r["checked"], r.get("excluded_minimized") or 0))
    print("ENV: dpi=%s scale=%s%% screen=%s screenshot=%s monitors=%d risks=%s"
          % (env.get("dpi_awareness"), env.get("scale_pct"), env.get("screen"),
             env.get("screenshot"), len(env.get("monitors") or []),
             env.get("risks")))
    if r.get("cdp"):
        print("CDP: ok=%s tabs=%s %s" % (r["cdp"]["ok"], r["cdp"]["tabs"],
                                         r["cdp"]["error"] or ""))
    for c in r["candidates"][:8]:
        print("   [%s] hwnd=%s %r exe=%s rect=%s"
              % (c["rule"], c["hwnd"], c["title"][:58], c["exe"], c["rect"]))