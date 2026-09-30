# -*- coding: utf-8 -*-
"""ENVIRONMENT REQUIREMENTS — read the VALUE, never hard-code it.

WHY THIS MODULE EXISTS (measured 2026-09-21)
--------------------------------------------
The user's diagnosis: "problem is environment setting didn't apply to necessary!!!
... hardcoded!! get by value never have fucking bull shit problem!"

Four defects in ONE day all had the same shape — a rule that was written down but
never enforced at the point of action:

  1. a session's source exists, but `_target_hwnd()` never read it
  2. `_real_to_image`'s comment says "never a hard-coded 1.2"; the very same call
     site pinned `maxWidth=1600`
  3. `f_new_session.py:7` recorded the OpenClaw foreground-steal bug; the call site
     preferred OpenClaw anyway
  4. DPI awareness was never SET; it arrived by accident from `import pyautogui`

CONTRAST, same day: `citation_discipline.assert_cited` REFUSED an uncitable
reference and the mistake never landed. A gate that refuses works; a comment that
documents does not.

So these checks do not describe the environment. They MEASURE it and return `ok`
false, which makes the caller refuse.

TWO KINDS OF "HARD-CODED" — this distinction matters:
  * a value that can be DERIVED from the machine and is written down anyway
    (`maxWidth=1600`, `1920x1080`, `total=67`) -> a defect; read it instead
  * a value that is a TAUTOLOGY (`{"kind":"APP"}`) -> not a defect, but NOT
    evidence either; using it as proof is circular
Both are handled here by READING what can be read, and by saying plainly when a
value is only an echo of the request rather than a measurement.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# desktop pins
# ---------------------------------------------------------------------------

# The ONLY reliable place. MEASURED 2026-09-21: the taskbar exposes NO
# item-bearing control (14 descendant windows; classes were MSTaskListWClass,
# MSTaskSwWClass, ReBarWindow32, TrayNotifyWnd ... and ZERO SysListView32 /
# ToolbarWindow32). The icons are DRAWN, so they cannot be read as UI. The pin
# FILES are the instrument — which is also good news: a file check is
# deterministic and does not depend on coordinates at all.
PINS_DIR = (Path(os.environ.get("APPDATA", "")) /
            "Microsoft" / "Internet Explorer" / "Quick Launch" /
            "User Pinned" / "TaskBar")

# A .lnk can hold its target as ASCII or UTF-16LE. MEASURED: a plain byte scan
# returns the SAME target as WScript.Shell for every pin that has an exe target
# (3/3), so no COM and no subprocess are needed. File Explorer returns nothing
# from either method because it uses a shell namespace id, not a path — reported
# as such rather than guessed.
_TARGET_ASCII = re.compile(rb"(?:[A-Za-z]:\\|\\\\)[^\x00]{2,200}?\.exe",
                           re.IGNORECASE)
_TARGET_UTF16 = re.compile(
    rb"(?:(?:[A-Za-z]:\\|\\\\)(?:[\x20-\x7e]\x00){2,200}?\.\x00e\x00x\x00e\x00)",
    re.IGNORECASE)


def read_pins() -> dict[str, Any]:
    """Every pinned taskbar item and the exe it points at, if it has one.

    Returns {ok, dir, count, pins:[{pin, target, has_target, encoding}], error}.
    `has_target=False` is reported, not hidden: File Explorer legitimately has no
    exe path, and treating "no path" as "missing pin" would be a false refusal.
    """
    out: dict[str, Any] = {"ok": False, "dir": str(PINS_DIR), "count": 0,
                           "pins": [], "error": None}
    if not PINS_DIR.is_dir():
        out["error"] = ("no taskbar pin directory at %s — the taskbar has no "
                        "pinned items on this profile" % PINS_DIR)
        return out
    for f in sorted(PINS_DIR.iterdir()):
        if f.suffix.lower() != ".lnk" or f.name.lower() == "desktop.ini":
            continue
        rec: dict[str, Any] = {"pin": f.name, "target": None,
                               "has_target": False, "encoding": None}
        try:
            raw = f.read_bytes()
        except OSError as e:
            rec["error"] = "%s: %s" % (type(e).__name__, e)
            out["pins"].append(rec)
            continue
        for label, rx, enc in (("ascii", _TARGET_ASCII, "latin-1"),
                               ("utf16", _TARGET_UTF16, "utf-16-le")):
            m = rx.search(raw)
            if m:
                txt = m.group(0)
                try:
                    txt = txt.decode(enc)
                except Exception:
                    continue
                txt = txt.replace("\x00", "")
                if txt:
                    rec["target"] = txt
                    rec["has_target"] = True
                    rec["encoding"] = label
                    break
        out["pins"].append(rec)
    out["count"] = len(out["pins"])
    out["ok"] = True
    return out


def pins_for(source_kind: str = "", source_ref: str = "") -> dict[str, Any]:
    """Do the pins satisfy a SOURCE? Position is not a factor — only presence.

    Matching mirrors `source_window.resolve_window`: an APP matches by exe BASE
    NAME (the stored path can drift — MEASURED: the stored ref was
    `...\\Application\\Doubao.exe` while the running image is
    `...\\Application\\app\\Doubao.exe`), a URL matches a browser exe.

    Returns {ok, required, matched, pins, reason}.
    """
    r = read_pins()
    out: dict[str, Any] = {"ok": False, "required": None, "matched": [],
                           "pins": r["pins"], "reason": None,
                           "pins_dir": r["dir"], "count": r["count"]}
    if not r["ok"]:
        out["reason"] = r["error"]
        return out
    kind = str(source_kind or "").strip().upper()
    ref = str(source_ref or "").strip()
    if not kind or not ref:
        # No source to satisfy -> report what IS pinned, and say plainly that a
        # list of pins is not proof of anything.
        out["reason"] = ("no source given: %d pinned item(s), but a pin list on its "
                         "own is NOT evidence that the required app is present"
                         % r["count"])
        out["ok"] = r["count"] > 0
        return out

    if kind == "APP":
        want = Path(ref).name.lower()
        out["required"] = want
        out["matched"] = [p for p in r["pins"]
                          if p.get("target")
                          and Path(p["target"]).name.lower() == want]
    else:
        # A URL source needs a BROWSER pinned. Which browser is not inferred here:
        # the source_ref is a URL, not a browser, so ANY pinned browser satisfies
        # "a browser is pinned" and the reason says exactly that.
        try:
            import source_window as sw
            browsers = sw._BROWSER_EXES
        except Exception:
            browsers = {"msedge.exe", "chrome.exe", "brave.exe", "firefox.exe"}
        out["required"] = "a pinned browser (%s)" % ", ".join(sorted(browsers))
        out["matched"] = [p for p in r["pins"]
                          if p.get("target")
                          and Path(p["target"]).name.lower() in browsers]
    out["ok"] = bool(out["matched"])
    if not out["ok"]:
        have = [p["pin"] for p in r["pins"]]
        out["reason"] = ("nothing pinned matches %s: required=%s, pinned=%s"
                         % (kind, out["required"], have))
    return out


# ---------------------------------------------------------------------------
# screenshot space
# ---------------------------------------------------------------------------

def check_screen_space() -> dict:
    """Every capture source must be in ONE space (1:1 with the screen).

    MEASURED 2026-09-21: `pyautogui` returned 1920x1080 while OpenClaw returned
    1600x900 (scale 1.2) from the SAME function, because the call site pinned
    `maxWidth=1600`. A rect applied in the wrong space lands in the wrong place
    while still looking deliberate.
    """
    try:
        import screenshot_space as ss
        sel = ss.selftest()
        sizes = {k: v["size"] for k, v in sel["sources"].items()}
        if not sel.get("same_space"):
            return _bad_es("screen_space",
                           "capture sources are NOT in one space: %s" % sizes,
                           sources=sel["sources"])
        if not sel.get("all_native"):
            return _bad_es("screen_space",
                           "a source is not 1:1 with the screen %s: scales %s"
                           % (sel["screen"],
                              {k: v["scale"] for k, v in sel["sources"].items()}),
                           sources=sel["sources"])
        return _ok_es("screen_space",
                      "all sources native %sx%s (scale 1.0)"
                      % tuple(sel["screen"]), sources=sel["sources"])
    except Exception as e:
        return _bad_es("screen_space", "%s: %s" % (type(e).__name__, e))


def check_dpi_pinned() -> dict:
    """DPI awareness must be SET, not inherited from an import side effect.

    MEASURED 2026-09-21: a bare process reports UNAWARE, and after
    `import pyautogui` it reports SYSTEM — pyautogui calls SetProcessDPIAware().
    That changes what GetWindowRect RETURNS (virtualized vs physical). At 100%
    scale the two coincide, which is why this stayed invisible; at 125% every
    mark would be off by the ratio.
    """
    try:
        import source_window as sw
        pin = sw.pin_dpi_awareness()
        # Pin FIRST, then re-read: the point is that it is set deliberately.
        now = sw._read_awareness()
        if not pin.get("pinned"):
            return _bad_es("dpi_pinned",
                           "DPI awareness could not be pinned (method=%s); rects "
                           "may be in a different space than the screenshot"
                           % pin.get("method"), pin=pin)
        if now == "UNAWARE":
            return _bad_es("dpi_pinned",
                           "DPI awareness is UNAWARE after pinning (method=%s)"
                           % pin.get("method"), pin=pin)
        return _ok_es("dpi_pinned", "awareness=%s (was %s, pinned via %s)"
                      % (now, pin.get("was"), pin.get("method")), pin=pin)
    except Exception as e:
        return _bad_es("dpi_pinned", "%s: %s" % (type(e).__name__, e))


def check_source_window(source_kind: str = "", source_ref: str = "") -> dict:
    """The session source must RESOLVE to a window. A no-match is a refusal.

    MEASURED 2026-09-20: `_target_hwnd()` called `pick_doubao_window()`
    unconditionally, so a session whose source was a URL captured the 豆包 window
    and reported ok — the LEVEL 1 gate then judged the WRONG PROGRAM.
    """
    kind = str(source_kind or "").strip().upper()
    ref = str(source_ref or "").strip()
    if not kind or not ref:
        return _bad_es("source_window",
                       "session has no source_kind/source_ref — the capture target "
                       "cannot be derived, so there is nothing to prove")
    try:
        import source_window as sw
        r = sw.resolve_window(kind, ref, allow_fallback=False)
        if not r.get("ok"):
            return _bad_es("source_window", r.get("detail") or "no match",
                           resolution={k: v for k, v in r.items()
                                       if k not in ("env", "candidates_seen")})
        return _ok_es("source_window",
                      "%s -> hwnd=%s %r" % (r["rule"], r["hwnd"],
                                            (r.get("window") or {}).get("title", "")[:44]),
                      resolution={"rule": r["rule"], "hwnd": r["hwnd"],
                                  "title": (r.get("window") or {}).get("title")})
    except Exception as e:
        return _bad_es("source_window", "%s: %s" % (type(e).__name__, e))


# ---------------------------------------------------------------------------
# local result helpers (namespaced so they cannot collide with f_env_preflight's
# own _ok / _bad, which are imported from there by callers)
# ---------------------------------------------------------------------------

def _ok_es(name: str, detail: str, **extra) -> dict:
    return {"name": name, "ok": True, "detail": detail, **extra}


def _bad_es(name: str, detail: str, **extra) -> dict:
    return {"name": name, "ok": False, "detail": detail, **extra}


# ---------------------------------------------------------------------------
# the aggregate the page calls on ENTER
# ---------------------------------------------------------------------------

def check_all(source_kind: str = "", source_ref: str = "",
              require_pins: bool = True) -> dict[str, Any]:
    """Every environment requirement, MEASURED. Returns the checklist.

    `ok` is True only when every required check passed. The caller must refuse to
    proceed otherwise — this function does not "advise", it reports a gate result.

    `require_pins=False` exists because a pin is an OPERATOR PREFERENCE, not a
    property of the machine: it is legitimate to run without pins, but then the
    checklist must say so rather than silently pass. Pass it explicitly so the
    choice is visible.
    """
    checks: list[dict] = [check_dpi_pinned(), check_screen_space()]
    if require_pins:
        pf = pins_for(source_kind, source_ref)
        checks.append(_ok_es("desktop_pins", pf["reason"] or
                             "%d matching pin(s)" % len(pf["matched"]),
                             matched=pf["matched"], pins=pf["pins"],
                             required=pf["required"]) if pf["ok"]
                      else _bad_es("desktop_pins", pf["reason"] or "no match",
                                   matched=pf["matched"], pins=pf["pins"],
                                   required=pf["required"]))
    if source_kind and source_ref:
        checks.append(check_source_window(source_kind, source_ref))
    blocking = [c for c in checks if not c["ok"]]
    return {
        "ok": not blocking,
        "checks": checks,
        "blocking": [c["name"] for c in blocking],
        "passed": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
        "require_pins": bool(require_pins),
        "source": {"kind": source_kind, "ref": source_ref},
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json

    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    ref = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(check_all(kind, ref), ensure_ascii=False, indent=2,
                     default=str))